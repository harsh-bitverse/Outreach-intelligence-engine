"""
Unit & Integration Tests for Phase 9:
End-to-End Delivery Failure Notification (DSN) Reconciliation, Classification,
Database & Excel Synchronization, and Pacing Validation.
Uses isolated temporary SQLite databases and Excel workbooks to prevent taining production data.
"""

import email
from email.message import EmailMessage
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import openpyxl
import pytest

from config.settings import settings
from src.database.connection import get_db
from src.database.workbook_sync import DynamicWorkbookSync
from src.mail.bounce_reconciler import (
    BounceClassifier,
    BounceReconciler,
    DeliveryStatusNotification,
    DSNParser,
    clean_email_address,
)
from src.mail.bounce_monitor import IMAPBounceMonitor
from src.scheduler.autonomous_campaign_loop import AutonomousCampaignLoop


@pytest.fixture
def isolated_env():
    """Create isolated SQLite database and Excel workbook for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        tmp_db = tmp_dir / "test_reconciliation.db"
        tmp_wb = tmp_dir / "test_workbook.xlsx"
        tmp_lock = tmp_dir / "test_scheduler.lock"

        # Copy master workbook and sqlite DB
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, tmp_wb)
        shutil.copy2(settings.SQLITE_DB_PATH, tmp_db)

        reconciler = BounceReconciler(db_path=tmp_db, workbook_path=tmp_wb)
        reconciler.init_database_tables()

        yield {
            "tmp_dir": tmp_dir,
            "db_path": tmp_db,
            "workbook_path": tmp_wb,
            "lock_path": tmp_lock,
            "reconciler": reconciler,
        }


def make_sample_dsn_bytes(
    recipient: str = "unknown.user@korea.ac.kr",
    status_code: str = "5.1.1",
    action: str = "failed",
    diagnostic_code: str = "smtp; 550 5.1.1 User unknown",
    orig_msg_id: str = "<test_123456@iitd.ac.in>",
    orig_subject: str = "Summer 2027 Research Internship — AI & Systems | IIT Delhi",
    bounce_mid: str = "<bounce_9999@mailserver.com>",
) -> bytes:
    """Helper to synthesize an RFC 3464 multipart/report DSN message in bytes."""
    msg = EmailMessage()
    msg["From"] = "Mail Delivery System <MAILER-DAEMON@mailstore.iitd.ac.in>"
    msg["To"] = "ch1251635@iitd.ac.in"
    msg["Subject"] = "Delivery Status Notification (Failure)"
    msg["Date"] = "Tue, 03 Nov 2026 05:00:00 +0530"
    msg["Message-ID"] = bounce_mid
    msg.set_type("multipart/report")
    msg.set_param("report-type", "delivery-status")

    # Part 1: Human readable explanation
    part1 = EmailMessage()
    part1.set_content(f"This is the mail system at host mailstore.iitd.ac.in.\nDelivery to <{recipient}> failed.")
    msg.attach(part1)

    # Part 2: message/delivery-status
    part2 = EmailMessage()
    part2.set_type("message/delivery-status")
    part2_text = (
        f"Reporting-MTA: dns; mailstore.iitd.ac.in\n\n"
        f"Final-Recipient: rfc822; {recipient}\n"
        f"Action: {action}\n"
        f"Status: {status_code}\n"
        f"Diagnostic-Code: {diagnostic_code}\n"
    )
    part2.set_content(part2_text)
    msg.attach(part2)

    # Part 3: message/rfc822
    part3 = EmailMessage()
    part3.set_type("message/rfc822")
    orig_text = (
        f"Message-ID: {orig_msg_id}\n"
        f"To: {recipient}\n"
        f"Subject: {orig_subject}\n"
        f"Date: Mon, 02 Nov 2026 10:00:00 +0530\n\n"
        f"Dear Professor,\n\nThis is an inquiry regarding Summer 2027 research."
    )
    part3.set_content(orig_text)
    msg.attach(part3)

    return msg.as_bytes()


# ------------------------------------------------------------------------------
# 1. Genuine message/delivery-status MIME parsing
# ------------------------------------------------------------------------------
def test_rfc3464_mime_parsing():
    """Verify DSNParser accurately extracts headers, delivery status, and original message."""
    raw = make_sample_dsn_bytes(
        recipient="test.prof@ethz.ch",
        status_code="5.1.10",
        action="failed",
        diagnostic_code="smtp; 550 5.1.10 RESOLVER.ADR.RecipientNotFound",
        orig_msg_id="<orig_abc123@iitd.ac.in>",
    )
    dsn = DSNParser.parse_message(folder="INBOX", uid=501, raw_bytes=raw)

    assert dsn is not None
    assert dsn.folder == "INBOX"
    assert dsn.uid == 501
    assert dsn.recipient_email == "test.prof@ethz.ch"
    assert dsn.orig_message_id == "<orig_abc123@iitd.ac.in>"
    assert dsn.status_code == "5.1.10"
    assert dsn.action == "failed"
    assert "RecipientNotFound" in dsn.diagnostic_code
    assert dsn.category == "BOUNCED_INVALID_ADDRESS"


# ------------------------------------------------------------------------------
# 2. 550 5.1.1 Invalid-mailbox classification
# ------------------------------------------------------------------------------
def test_invalid_mailbox_classification():
    """Verify 550 5.1.1, user unknown, mailbox does not exist are categorized as BOUNCED_INVALID_ADDRESS."""
    cat1 = BounceClassifier.classify("failed", "5.1.1", "550 5.1.1 User unknown")
    assert cat1 == "BOUNCED_INVALID_ADDRESS"

    cat2 = BounceClassifier.classify("failed", "5.1.10", "RESOLVER.ADR.RecipientNotFound")
    assert cat2 == "BOUNCED_INVALID_ADDRESS"

    cat3 = BounceClassifier.classify("failed", "5.0.0", "The email account that you tried to reach does not exist")
    assert cat3 == "BOUNCED_INVALID_ADDRESS"

    cat4 = BounceClassifier.classify("failed", "5.1.3", "Bad recipient address syntax")
    assert cat4 == "BOUNCED_INVALID_ADDRESS"


# ------------------------------------------------------------------------------
# 3. Other permanent 5.x.x failures (Hop count, policy refusal, DNS error)
# ------------------------------------------------------------------------------
def test_permanent_other_classification():
    """Verify 554 5.4.14 hop count exceeded, 554 5.7.1 relay denied, and DNS errors are BOUNCED_PERMANENT_OTHER."""
    cat_loop = BounceClassifier.classify("failed", "5.4.14", "554 5.4.14 Hop count exceeded - possible mail loop")
    assert cat_loop == "BOUNCED_PERMANENT_OTHER"

    cat_relay = BounceClassifier.classify("failed", "5.0.0", "554 5.7.1 Relay access denied")
    assert cat_relay == "BOUNCED_PERMANENT_OTHER"

    cat_dns = BounceClassifier.classify("failed", "5.0.0", "5.1.2 - Bad destination host 'DNS Hard Error looking up host'")
    assert cat_dns == "BOUNCED_PERMANENT_OTHER"


# ------------------------------------------------------------------------------
# 4. Temporary 4.x.x failures
# ------------------------------------------------------------------------------
def test_temporary_delivery_failure_classification():
    """Verify 4.x.x and delayed action notices are categorized as TEMPORARY_DELIVERY_FAILURE."""
    cat_temp = BounceClassifier.classify("delayed", "4.0.0", "4.4.2 - Bad connection 'timeout'")
    assert cat_temp == "TEMPORARY_DELIVERY_FAILURE"

    cat_temp2 = BounceClassifier.classify("failed", "4.4.1", "Connection timed out")
    assert cat_temp2 == "TEMPORARY_DELIVERY_FAILURE"


# ------------------------------------------------------------------------------
# 5. Repeated and duplicate DSNs
# ------------------------------------------------------------------------------
def test_duplicate_dsn_deduplication(isolated_env):
    """Verify repeated DSNs for the same recipient are deduplicated in reconciliation."""
    reconciler = isolated_env["reconciler"]

    raw1 = make_sample_dsn_bytes(recipient="test.dup@korea.ac.kr", bounce_mid="<b1@m.com>")
    raw2 = make_sample_dsn_bytes(recipient="test.dup@korea.ac.kr", bounce_mid="<b2@m.com>")

    dsn1 = DSNParser.parse_message(folder="INBOX", uid=701, raw_bytes=raw1)
    dsn2 = DSNParser.parse_message(folder="INBOX", uid=702, raw_bytes=raw2)

    stats = reconciler.reconcile_notifications([dsn1, dsn2])
    assert stats["total_inspected"] == 2
    assert stats["unique_dsns"] == 1


# ------------------------------------------------------------------------------
# 6. Bounces without original Message-ID
# ------------------------------------------------------------------------------
def test_bounce_without_orig_message_id(isolated_env):
    """Verify bounces missing original Message-ID still match via recipient email."""
    reconciler = isolated_env["reconciler"]
    db_path = isolated_env["db_path"]

    # Insert a test professor into the isolated DB
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO professors (
                professor_id, university_name, country, full_name, email,
                outreach_status, created_at, updated_at
            ) VALUES ('prof_nomid_test', 'Test Uni', 'Country', 'Dr. No Mid', 'nomid@testuni.edu', 'SENT', ?, ?);
        """, (now_iso, now_iso))
        conn.commit()

    dsn = DeliveryStatusNotification(
        folder="INBOX",
        uid=801,
        bounce_message_id="<b801@mail.com>",
        bounce_date="Tue, 03 Nov 2026",
        bounce_from="MAILER-DAEMON",
        bounce_subject="Delivery Status Notification",
        action="failed",
        status_code="5.1.1",
        diagnostic_code="User unknown",
        final_recipient="rfc822; nomid@testuni.edu",
        orig_message_id=None,  # No original Message-ID!
    )

    matched = reconciler.match_professor(dsn)
    assert matched is not None
    assert matched["professor_id"] == "prof_nomid_test"
    assert matched["email"] == "nomid@testuni.edu"


# ------------------------------------------------------------------------------
# 7. Unmatched / ambiguous recipient records
# ------------------------------------------------------------------------------
def test_unmatched_recipient_handling(isolated_env):
    """Verify unknown recipients are recorded in ledger without crashing or assigning wrong professor."""
    reconciler = isolated_env["reconciler"]
    db_path = isolated_env["db_path"]

    dsn = DeliveryStatusNotification(
        folder="INBOX",
        uid=901,
        bounce_message_id="<b901@mail.com>",
        bounce_date="Tue, 03 Nov 2026",
        bounce_from="MAILER-DAEMON",
        bounce_subject="Delivery Status Notification",
        action="failed",
        status_code="5.1.1",
        diagnostic_code="User unknown",
        final_recipient="rfc822; unknown.stranger@nonexistent.edu",
    )

    stats = reconciler.reconcile_notifications([dsn])
    assert stats["unmatched"] == 1
    assert stats["matched_professors"] == 0

    # Verify recorded in delivery_status_ledger
    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM delivery_status_ledger WHERE recipient_email = 'unknown.stranger@nonexistent.edu';").fetchone()
        assert row is not None
        assert row["professor_id"] is None


# ------------------------------------------------------------------------------
# 8. Correct synchronization of SQLite and both Excel sheets
# ------------------------------------------------------------------------------
def test_synchronization_sqlite_and_excel(isolated_env):
    """Verify SQLite and both Excel sheets are transactionally synchronized for invalid addresses."""
    reconciler = isolated_env["reconciler"]
    db_path = isolated_env["db_path"]
    wb_path = isolated_env["workbook_path"]

    test_email = "sync.prof@testuni.edu"
    prof_id = "prof_sync_test"

    # Seed isolated DB and Excel
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db(db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO professors (
                professor_id, university_name, country, full_name, email,
                outreach_status, email_verification_status, created_at, updated_at
            ) VALUES (?, 'Sync Uni', 'Country', 'Sync Professor', ?, 'SENT', 'VERIFIED', ?, ?);
        """, (prof_id, test_email, now_iso, now_iso))
        conn.execute("""
            INSERT OR REPLACE INTO outreach_queue (
                queue_id, professor_id, draft_id, time_block_id, window_start_ist,
                window_end_ist, sequence_order, status, attempts, created_at, updated_at
            ) VALUES ('q_sync_test', ?, 'draft_sync_test', 'BLOCK_01', '01:00', '02:00', 1, 'SENT', 1, ?, ?);
        """, (prof_id, now_iso, now_iso))
        conn.commit()

    wb = openpyxl.load_workbook(str(wb_path))
    ws_tb = wb["Time Blocks & Distribution"]
    ws_tb.cell(10, 8).value = test_email
    ws_tb.cell(10, 9).value = "VERIFIED"
    ws_tb.cell(10, 25).value = "SENT"
    ws_tb.cell(10, 29).value = "SENT"

    ws_prof = wb["Professor Database"]
    ws_prof.cell(5, 8).value = test_email
    ws_prof.cell(5, 9).value = "VERIFIED"
    ws_prof.cell(5, 19).value = "SENT"
    wb.save(str(wb_path))
    wb.close()

    # Reconcile bounce
    dsn = DeliveryStatusNotification(
        folder="INBOX",
        uid=1001,
        bounce_message_id="<b1001@mail.com>",
        bounce_date="Tue, 03 Nov 2026",
        bounce_from="MAILER-DAEMON",
        bounce_subject="Delivery Status Notification",
        action="failed",
        status_code="5.1.1",
        diagnostic_code="550 5.1.1 Mailbox does not exist",
        final_recipient=f"rfc822; {test_email}",
    )
    reconciler.reconcile_notifications([dsn])

    # 1. Verify SQLite
    with get_db(db_path) as conn:
        p_row = conn.execute("SELECT * FROM professors WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert p_row["outreach_status"] == "BOUNCED_INVALID_ADDRESS"
        assert p_row["email_verification_status"] == "INVALID"
        assert "[BOUNCED_INVALID_ADDRESS]" in p_row["notes"]

        q_row = conn.execute("SELECT * FROM outreach_queue WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert q_row["status"] == "BOUNCED_INVALID_ADDRESS"

    # 2. Verify Excel
    wb_verify = openpyxl.load_workbook(str(wb_path), read_only=True)
    ws_tb_v = wb_verify["Time Blocks & Distribution"]
    assert ws_tb_v.cell(10, 9).value == "INVALID"
    assert ws_tb_v.cell(10, 25).value == "BOUNCED_INVALID_ADDRESS"
    assert ws_tb_v.cell(10, 29).value == "BOUNCED_INVALID_ADDRESS"

    ws_prof_v = wb_verify["Professor Database"]
    assert ws_prof_v.cell(5, 9).value == "INVALID"
    assert ws_prof_v.cell(5, 19).value == "BOUNCED_INVALID_ADDRESS"
    wb_verify.close()


# ------------------------------------------------------------------------------
# 9. Preservation of historical SMTP SENT records
# ------------------------------------------------------------------------------
def test_historical_sent_records_preserved(isolated_env):
    """Verify reconciliation preserves historical email_messages entries without deletion."""
    db_path = isolated_env["db_path"]
    with get_db(db_path) as conn:
        before_cnt = conn.execute("SELECT COUNT(*) FROM email_messages;").fetchone()[0]

    reconciler = isolated_env["reconciler"]
    dsn = DeliveryStatusNotification(
        folder="INBOX",
        uid=1101,
        bounce_message_id="<b1101@m.com>",
        bounce_date="Tue, 03 Nov 2026",
        bounce_from="MAILER-DAEMON",
        bounce_subject="Delivery Status Notification",
        action="failed",
        status_code="5.1.1",
        diagnostic_code="User unknown",
        final_recipient="rfc822; hutchins@unimelb.edu.au",
    )
    reconciler.reconcile_notifications([dsn])

    with get_db(db_path) as conn:
        after_cnt = conn.execute("SELECT COUNT(*) FROM email_messages;").fetchone()[0]

    assert after_cnt == before_cnt  # Zero deletion!


# ------------------------------------------------------------------------------
# 10. Prevention of future dispatch to invalid addresses
# ------------------------------------------------------------------------------
def test_prevention_of_future_dispatch(isolated_env):
    """Verify candidate selection strictly excludes professors with BOUNCED status or INVALID verification."""
    wb_path = isolated_env["workbook_path"]
    db_path = isolated_env["db_path"]
    lock_path = isolated_env["lock_path"]

    sync = DynamicWorkbookSync(workbook_path=wb_path)
    loop = AutonomousCampaignLoop(workbook_path=wb_path, sync=sync, lock_path=lock_path)

    # Set Nicholas Hutchins as BOUNCED_INVALID_ADDRESS in SQLite and Excel
    with get_db(db_path) as conn:
        conn.execute("UPDATE professors SET outreach_status = 'BOUNCED_INVALID_ADDRESS', email_verification_status = 'INVALID' WHERE LOWER(email) = 'hutchins@unimelb.edu.au';")
        conn.commit()

    candidates = loop.find_eligible_professors_in_block("BLOCK_01", limit=50)
    for c in candidates:
        assert c["email"].lower() != "hutchins@unimelb.edu.au"


# ------------------------------------------------------------------------------
# 11. Proposed replacement address handling and storage
# ------------------------------------------------------------------------------
def test_proposed_replacement_storage(isolated_env):
    """Verify researched replacement email addresses are persisted in proposed_email_replacements."""
    reconciler = isolated_env["reconciler"]
    db_path = isolated_env["db_path"]

    reconciler.record_proposed_replacement(
        professor_id="prof_test_rep",
        professor_name="Prof. Test Replacement",
        original_bounced_email="old.bounced@unimelb.edu.au",
        proposed_email="new.verified@unimelb.edu.au",
        replacement_status="PROPOSED_REPLACEMENT",
        current_institution="The University of Melbourne",
        department="Mechanical Engineering",
        source_url="https://findanexpert.unimelb.edu.au/profile/test",
        evidence_notes="Verified on University of Melbourne Find an Expert profile.",
    )

    with get_db(db_path) as conn:
        row = conn.execute("SELECT * FROM proposed_email_replacements WHERE professor_id = 'prof_test_rep';").fetchone()
        assert row is not None
        assert row["proposed_email"] == "new.verified@unimelb.edu.au"
        assert row["replacement_status"] == "PROPOSED_REPLACEMENT"
        assert row["current_institution"] == "The University of Melbourne"


# ------------------------------------------------------------------------------
# 12. Idempotent repeated IMAP scans
# ------------------------------------------------------------------------------
def test_idempotent_reconciliation(isolated_env):
    """Verify running reconciliation multiple times is strictly idempotent."""
    reconciler = isolated_env["reconciler"]
    raw = make_sample_dsn_bytes(recipient="test.idem@korea.ac.kr", bounce_mid="<idem@m.com>")
    dsn = DSNParser.parse_message(folder="INBOX", uid=1201, raw_bytes=raw)

    stats1 = reconciler.reconcile_notifications([dsn])
    stats2 = reconciler.reconcile_notifications([dsn])

    assert stats1["unique_dsns"] == 1
    assert stats2["unique_dsns"] == 1


# ------------------------------------------------------------------------------
# 13. Pacing configuration: 10-message batch and 300-second cooling break
# ------------------------------------------------------------------------------
def test_pacing_configuration():
    """Verify conservative SMTP pacing parameters configured for Session 11."""
    assert settings.BATCH_SIZE == 10
    assert settings.COOLING_BREAK_SECONDS == 300
    assert settings.MIN_INTERVAL_SECONDS_BETWEEN_EMAILS >= 25
    assert settings.DAILY_MAX_OUTREACH == 200
    assert settings.DRY_RUN is True  # Paused safely
    assert settings.GATEWAY_ENABLED is False  # Safe pause
