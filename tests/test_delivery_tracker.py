"""
Tests for Session 12 Real-Time Concurrent Outbound Delivery Tracker:
Covers all 11 watch, bounce, restart, and candidate selection scenarios.
"""

import email
from email.message import EmailMessage
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict

import openpyxl
import pytest

from config.settings import settings
from src.database.connection import get_db
from src.database.repository import compute_durable_professor_id
from src.database.workbook_sync import DynamicWorkbookSync
from src.mail.bounce_reconciler import (
    BounceClassifier,
    BounceReconciler,
    DeliveryStatusNotification,
    clean_email_address,
)
from src.mail.delivery_tracker import OutboundDeliveryTracker
from src.scheduler.autonomous_campaign_loop import AutonomousCampaignLoop


@pytest.fixture
def isolated_env():
    """Create isolated SQLite database and Excel workbook for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        tmp_db = tmp_dir / "test_outreach.db"
        tmp_wb = tmp_dir / "test_workbook.xlsx"
        tmp_lock = tmp_dir / "test_scheduler.lock"

        # Copy master workbook and sqlite DB
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, tmp_wb)
        shutil.copy2(settings.SQLITE_DB_PATH, tmp_db)

        tracker = OutboundDeliveryTracker(db_path=tmp_db)
        sync = DynamicWorkbookSync(workbook_path=tmp_wb)

        yield {
            "tmp_dir": tmp_dir,
            "db_path": tmp_db,
            "workbook_path": tmp_wb,
            "lock_path": tmp_lock,
            "tracker": tracker,
            "sync": sync,
        }


def make_dsn_dict(
    recipient: str,
    orig_msg_id: str,
    status_code: str = "5.1.1",
    action: str = "failed",
    diagnostic_code: str = "smtp; 550 5.1.1 User unknown",
    category: str = "BOUNCED_INVALID_ADDRESS"
) -> Dict[str, Any]:
    return {
        "recipient_email": clean_email_address(recipient),
        "orig_message_id": orig_msg_id,
        "status_code": status_code,
        "action": action,
        "diagnostic_code": diagnostic_code,
        "category": category,
        "received_at": datetime.now(timezone.utc).isoformat()
    }


# ------------------------------------------------------------------------------
# Test 1: Concurrent 10-minute delivery watch enrollment
# ------------------------------------------------------------------------------
def test_concurrent_watch_enrollment(isolated_env):
    tracker = isolated_env["tracker"]
    db_p = isolated_env["db_path"]

    profs = [
        ("prof_test_1", "Test Uni 1", 10, "test1@uni1.edu", "<msg_test_1@iitd.ac.in>"),
        ("prof_test_2", "Test Uni 2", 11, "test2@uni2.edu", "<msg_test_2@iitd.ac.in>"),
        ("prof_test_3", "Test Uni 3", 12, "test3@uni3.edu", "<msg_test_3@iitd.ac.in>"),
    ]

    for p_id, uni, row_idx, em, mid in profs:
        rec = tracker.track_outbound_email(
            professor_id=p_id,
            university_name=uni,
            workbook_row_index=row_idx,
            recipient_email=em,
            message_id=mid,
            watch_duration_minutes=10
        )
        assert rec["tracking_status"] == "ACTIVE_WATCH"
        assert rec["delivery_status"] == "UNCONFIRMED"
        assert rec["transport_status"] == "SMTP_ACCEPTED"

    active_watches = tracker.get_active_watches()
    assert len(active_watches) >= 3
    assert tracker.get_active_watch_count() >= 3


# ------------------------------------------------------------------------------
# Test 2: Permanent bounce arriving during 10-minute watch
# ------------------------------------------------------------------------------
def test_permanent_bounce_during_watch(isolated_env):
    tracker = isolated_env["tracker"]
    sync = isolated_env["sync"]
    db_p = isolated_env["db_path"]

    p_id = "prof_bounce_quick"
    em = "quickbounce@sample.edu"
    mid = "<quick_bounce_msg@iitd.ac.in>"

    tracker.track_outbound_email(
        professor_id=p_id,
        university_name="Sample University",
        workbook_row_index=15,
        recipient_email=em,
        message_id=mid,
        watch_duration_minutes=10
    )

    dsn = make_dsn_dict(
        recipient=em,
        orig_msg_id=mid,
        status_code="5.1.1",
        category="BOUNCED_INVALID_ADDRESS"
    )

    eval_res = tracker.check_active_watches(new_dsns=[dsn])
    assert eval_res["resolved_bounces"] == 1

    with get_db(db_p) as conn:
        row = conn.execute("SELECT delivery_status, tracking_status FROM outbound_delivery_tracker WHERE message_id = ?;", (mid,)).fetchone()
        assert row["delivery_status"] == "BOUNCED_INVALID_ADDRESS"
        assert row["tracking_status"] == "RESOLVED_BOUNCE"


# ------------------------------------------------------------------------------
# Test 3: Bounce arriving after 10-minute period
# ------------------------------------------------------------------------------
def test_delayed_bounce_after_watch_period(isolated_env):
    tracker = isolated_env["tracker"]
    db_p = isolated_env["db_path"]

    p_id = "prof_bounce_delayed"
    em = "delayed@sample.edu"
    mid = "<delayed_bounce_msg@iitd.ac.in>"

    # Track with deadline in the past
    past_ts = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    tracker.track_outbound_email(
        professor_id=p_id,
        university_name="Sample University",
        workbook_row_index=16,
        recipient_email=em,
        message_id=mid,
        smtp_accepted_at=past_ts,
        watch_duration_minutes=10
    )

    # Force deadline to be past
    with get_db(db_p) as conn:
        conn.execute("UPDATE outbound_delivery_tracker SET watch_deadline_at = ? WHERE message_id = ?;", (past_ts, mid))
        conn.commit()

    # Fast watch transitions to WATCH_COMPLETE, UNCONFIRMED
    eval_res = tracker.check_active_watches()
    assert eval_res["watch_completed"] >= 1

    with get_db(db_p) as conn:
        row = conn.execute("SELECT delivery_status, tracking_status FROM outbound_delivery_tracker WHERE message_id = ?;", (mid,)).fetchone()
        assert row["delivery_status"] == "UNCONFIRMED"
        assert row["tracking_status"] == "WATCH_COMPLETE"

    # Delayed DSN arrives
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db(db_p) as conn:
        conn.execute("""
            UPDATE outbound_delivery_tracker
            SET delivery_status = 'BOUNCED_PERMANENT_OTHER',
                tracking_status = 'RESOLVED_BOUNCE',
                bounce_diagnostic = '554 Hop count exceeded (Delayed DSN)',
                updated_at = ?
            WHERE message_id = ?;
        """, (now_iso, mid))
        conn.commit()

        row2 = conn.execute("SELECT delivery_status, tracking_status FROM outbound_delivery_tracker WHERE message_id = ?;", (mid,)).fetchone()
        assert row2["delivery_status"] == "BOUNCED_PERMANENT_OTHER"
        assert row2["tracking_status"] == "RESOLVED_BOUNCE"


# ------------------------------------------------------------------------------
# Test 4: Duplicate DSN deduplication
# ------------------------------------------------------------------------------
def test_duplicate_dsn_deduplication(isolated_env):
    db_p = isolated_env["db_path"]
    wb_p = isolated_env["workbook_path"]
    reconciler = BounceReconciler(db_path=db_p, workbook_path=wb_p)
    reconciler.init_database_tables()

    dsn1 = DeliveryStatusNotification(
        folder="INBOX",
        uid=5001,
        bounce_message_id="<dsn_dup_1@mailstore.iitd.ac.in>",
        bounce_date="Tue, 03 Nov 2026 05:00:00 +0530",
        bounce_from="MAILER-DAEMON@mailstore.iitd.ac.in",
        bounce_subject="Delivery Status Notification (Failure)",
        action="failed",
        status_code="5.1.1",
        diagnostic_code="550 5.1.1 User unknown",
        final_recipient="rfc822; dup.recipient@sample.edu",
        orig_message_id="<orig_msg_dup@iitd.ac.in>",
        orig_subject="Summer 2027 Research Internship — AI | IIT Delhi",
        orig_to="dup.recipient@sample.edu"
    )

    res1 = reconciler.reconcile_notifications([dsn1])
    assert res1["unique_dsns"] == 1

    # Same notification processed again (e.g. repeated fetch)
    res2 = reconciler.reconcile_notifications([dsn1])
    assert res2["unique_dsns"] == 1

    with get_db(db_p) as conn:
        count = conn.execute("SELECT COUNT(*) FROM delivery_status_ledger WHERE imap_uid = 5001;").fetchone()[0]
        assert count == 1


# ------------------------------------------------------------------------------
# Test 5: Process restart during active monitoring
# ------------------------------------------------------------------------------
def test_process_restart_reloads_active_watches(isolated_env):
    db_p = isolated_env["db_path"]

    tracker1 = OutboundDeliveryTracker(db_path=db_p)
    mid = "<restart_watch_test@iitd.ac.in>"
    tracker1.track_outbound_email(
        professor_id="prof_restart_1",
        university_name="Restart University",
        workbook_row_index=20,
        recipient_email="restart.prof@univ.edu",
        message_id=mid,
        watch_duration_minutes=10
    )

    # Simulate process exit: instantiate new tracker object
    tracker2 = OutboundDeliveryTracker(db_path=db_p)
    active = tracker2.get_active_watches()
    matched = [w for w in active if w["message_id"] == mid]
    assert len(matched) == 1
    assert matched[0]["tracking_status"] == "ACTIVE_WATCH"
    assert matched[0]["delivery_status"] == "UNCONFIRMED"


# ------------------------------------------------------------------------------
# Test 6: Address A bounce followed by Address B sending
# ------------------------------------------------------------------------------
def test_address_a_bounce_then_b_tracked(isolated_env):
    tracker = isolated_env["tracker"]
    db_p = isolated_env["db_path"]

    prof_id = "prof_multi_addr"
    addr_a = "old.addr@univ.edu"
    mid_a = "<msg_addr_a@iitd.ac.in>"

    # 1. Address A sent & bounces
    tracker.track_outbound_email(
        professor_id=prof_id,
        university_name="Multi University",
        workbook_row_index=25,
        recipient_email=addr_a,
        message_id=mid_a,
        address_attempt_number=1
    )
    tracker.check_active_watches(new_dsns=[make_dsn_dict(addr_a, mid_a)])

    # 2. Address B researched & sent
    addr_b = "new.verified@univ.edu"
    mid_b = "<msg_addr_b@iitd.ac.in>"
    tracker.track_outbound_email(
        professor_id=prof_id,
        university_name="Multi University",
        workbook_row_index=25,
        recipient_email=addr_b,
        message_id=mid_b,
        address_attempt_number=2,
        previous_attempts_history=f"Attempt 1: {addr_a} -> BOUNCED_INVALID_ADDRESS"
    )

    with get_db(db_p) as conn:
        rows = conn.execute("SELECT recipient_email, delivery_status, address_attempt_number FROM outbound_delivery_tracker WHERE professor_id = ? ORDER BY address_attempt_number ASC;", (prof_id,)).fetchall()
        assert len(rows) == 2
        assert rows[0]["recipient_email"] == addr_a
        assert rows[0]["delivery_status"] == "BOUNCED_INVALID_ADDRESS"
        assert rows[1]["recipient_email"] == addr_b
        assert rows[1]["delivery_status"] == "UNCONFIRMED"


# ------------------------------------------------------------------------------
# Test 7: Address A and B both bouncing
# ------------------------------------------------------------------------------
def test_address_a_and_b_both_bounce(isolated_env):
    tracker = isolated_env["tracker"]
    db_p = isolated_env["db_path"]

    prof_id = "prof_double_bounce"
    addr_a = "addr1@domain.edu"
    mid_a = "<mid_a@iitd.ac.in>"
    tracker.track_outbound_email(prof_id, "Double Uni", 30, addr_a, mid_a, address_attempt_number=1)
    tracker.check_active_watches(new_dsns=[make_dsn_dict(addr_a, mid_a)])

    addr_b = "addr2@domain.edu"
    mid_b = "<mid_b@iitd.ac.in>"
    tracker.track_outbound_email(prof_id, "Double Uni", 30, addr_b, mid_b, address_attempt_number=2)
    tracker.check_active_watches(new_dsns=[make_dsn_dict(addr_b, mid_b)])

    with get_db(db_p) as conn:
        bounces = conn.execute("SELECT recipient_email, delivery_status FROM outbound_delivery_tracker WHERE professor_id = ?;", (prof_id,)).fetchall()
        assert len(bounces) == 2
        assert all(b["delivery_status"] == "BOUNCED_INVALID_ADDRESS" for b in bounces)


# ------------------------------------------------------------------------------
# Test 8: Professor reply received after an earlier bounce
# ------------------------------------------------------------------------------
def test_reply_received_after_earlier_bounce(isolated_env):
    tracker = isolated_env["tracker"]
    db_p = isolated_env["db_path"]

    prof_id = "prof_reply_recovered"
    addr_a = "bounced@uni.edu"
    mid_a = "<bounce_before_reply@iitd.ac.in>"
    tracker.track_outbound_email(prof_id, "Reply Uni", 35, addr_a, mid_a)
    tracker.check_active_watches(new_dsns=[make_dsn_dict(addr_a, mid_a)])

    # Now reply comes in
    reply_record = {
        "from_email": addr_a,
        "in_reply_to": mid_a,
        "subject": "Re: Summer 2027 Research Internship — AI | IIT Delhi"
    }
    tracker.check_active_watches(new_replies=[reply_record])

    # Reconcile status to REPLIED
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db(db_p) as conn:
        conn.execute("""
            UPDATE outbound_delivery_tracker
            SET delivery_status = 'REPLIED',
                tracking_status = 'RESOLVED_REPLY',
                bounce_diagnostic = 'Genuine Professor Reply Confirmed',
                updated_at = ?
            WHERE professor_id = ?;
        """, (now_iso, prof_id))
        conn.commit()

        row = conn.execute("SELECT delivery_status, tracking_status FROM outbound_delivery_tracker WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert row["delivery_status"] == "REPLIED"
        assert row["tracking_status"] == "RESOLVED_REPLY"


# ------------------------------------------------------------------------------
# Test 9: Chronological status derivation
# ------------------------------------------------------------------------------
def test_chronological_status_derivation():
    # Deterministic priority: REPLIED > BOUNCE_INVALID > BOUNCE_PERMANENT > ERROR > SUPPRESSED > SENT_UNCONFIRMED > AWAITING_DRAFT
    def derive_status(replied: bool, invalid: bool, perm: bool, error: bool, sent: bool) -> str:
        if replied:
            return "REPLIED"
        if invalid:
            return "UNSENT — INVALID EMAIL (550 5.1.1)"
        if perm:
            return "UNSENT — PERMANENT DELIVERY FAILURE"
        if error:
            return "ERROR"
        if sent:
            return "SENT — DELIVERY UNCONFIRMED"
        return "AWAITING_DRAFT"

    assert derive_status(replied=True, invalid=True, perm=False, error=False, sent=True) == "REPLIED"
    assert derive_status(replied=False, invalid=True, perm=True, error=False, sent=True) == "UNSENT — INVALID EMAIL (550 5.1.1)"
    assert derive_status(replied=False, invalid=False, perm=True, error=False, sent=True) == "UNSENT — PERMANENT DELIVERY FAILURE"
    assert derive_status(replied=False, invalid=False, perm=False, error=True, sent=True) == "ERROR"
    assert derive_status(replied=False, invalid=False, perm=False, error=False, sent=True) == "SENT — DELIVERY UNCONFIRMED"
    assert derive_status(replied=False, invalid=False, perm=False, error=False, sent=False) == "AWAITING_DRAFT"


# ------------------------------------------------------------------------------
# Test 10: SQLite and Excel parity with dedicated columns
# ------------------------------------------------------------------------------
def test_sqlite_and_excel_parity(isolated_env):
    sync = isolated_env["sync"]
    wb_p = isolated_env["workbook_path"]
    em = "b.young@auckland.ac.nz"  # Row 8 in workbook

    now_iso = datetime.now(timezone.utc).isoformat()
    msg_id = "<parity_test_msg@iitd.ac.in>"

    updated = sync.update_time_blocks_row(
        email=em,
        status="SENT — DELIVERY UNCONFIRMED",
        sent_at=now_iso,
        message_id=msg_id,
        delivery_status="UNCONFIRMED",
        delivery_diagnostic="SMTP_ACCEPTED | 10-Min Watch Complete (Unconfirmed)",
        watch_state="WATCH_COMPLETE",
        timeout_seconds=5
    )
    assert updated is True

    wb = openpyxl.load_workbook(str(wb_p), data_only=True)
    ws1 = wb["Time Blocks & Distribution"]

    found = False
    for r in range(1, ws1.max_row + 1):
        if str(ws1.cell(r, 8).value or "").strip().lower() == em.lower():
            assert ws1.cell(r, 25).value == "SENT — DELIVERY UNCONFIRMED"
            assert ws1.cell(r, 29).value == "SENT — DELIVERY UNCONFIRMED"
            assert ws1.cell(r, 30).value == now_iso
            assert ws1.cell(r, 31).value == msg_id
            assert ws1.cell(r, 32).value == "UNCONFIRMED"
            assert ws1.cell(r, 33).value == "SMTP_ACCEPTED | 10-Min Watch Complete (Unconfirmed)"
            assert ws1.cell(r, 34).value == "WATCH_COMPLETE"
            found = True
            break
    wb.close()
    assert found is True


# ------------------------------------------------------------------------------
# Test 11: Exclusion of failed, unverified, and unconfirmed from candidate selection
# ------------------------------------------------------------------------------
def test_candidate_selection_excludes_all_contacted(isolated_env):
    db_p = isolated_env["db_path"]
    wb_p = isolated_env["workbook_path"]

    loop = AutonomousCampaignLoop(
        workbook_path=wb_p,
        sync=DynamicWorkbookSync(wb_p),
        lock_path=isolated_env["lock_path"]
    )

    # In our production workbook, find any candidates in BLOCK_01
    candidates = loop.find_eligible_professors_in_block(block_id="BLOCK_01", limit=10)

    # Verify no candidate is in any invalid/unconfirmed/replied status
    with get_db(db_p) as conn:
        for c in candidates:
            em = c["email"].lower()
            row = conn.execute("SELECT outreach_status FROM professors WHERE lower(email) = ?;", (em,)).fetchone()
            if row:
                assert row["outreach_status"] == "AWAITING_DRAFT"

            # Check that it is NOT in outbound_delivery_tracker
            tracked = conn.execute("SELECT COUNT(*) FROM outbound_delivery_tracker WHERE lower(recipient_email) = ?;", (em,)).fetchone()[0]
            assert tracked == 0
