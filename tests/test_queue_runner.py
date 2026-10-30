"""
Unit and Integration Tests for Autonomous Queue Runner, Durable Queue Persistence,
Crash Recovery, 90-Minute Window Evaluation, Cadence Cooling Breaks,
and Suppression of the 6 Reply-Received Contacts.
"""

import tempfile
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

from config.settings import settings
from src.database.connection import get_db, initialize_database
from src.database.models import Professor, EmailMessage
from src.database.repository import OutreachRepository
from src.mail.sending_gateway import SendingGateway
from src.scheduler.queue_runner import (
    AutonomousQueueRunner,
    REPLY_RECEIVED_EXCLUSIONS,
    TIME_BLOCK_WINDOWS
)


@pytest.fixture
def queue_test_env():
    """Create an isolated test SQLite DB and repository."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_queue.db"
        initialize_database(db_path)
        with get_db(db_path) as conn:
            repo = OutreachRepository(conn)
            runner = AutonomousQueueRunner()
            yield repo, runner, db_path


def test_reply_received_suppressions_enforced(queue_test_env):
    """Verify that all 6 confirmed reply-received contacts are strictly excluded."""
    repo, runner, _ = queue_test_env

    # Seed the suppressions
    runner.seed_reply_received_suppressions(repo)

    confirmed_reply_emails = [
        "han.fan@tum.de",
        "hojin.lee@tum.de",
        "lukas.oehler@tum.de",
        "tjtkoski@kth.se",
        "fontina.petrakopoulou@tu-berlin.de",
        "info@sitegeist-robotics.com"
    ]

    for email in confirmed_reply_emails:
        is_supp, reason = runner.is_suppressed_or_excluded(repo, email)
        assert is_supp is True, f"Expected {email} to be suppressed!"
        assert reason is not None
        assert "REPLY_RECEIVED" in reason

    # Verify that Abigail Bodner and Percy Liang are NOT suppressed
    is_bodner_supp, _ = runner.is_suppressed_or_excluded(repo, "bodner@mit.edu")
    is_liang_supp, _ = runner.is_suppressed_or_excluded(repo, "pliang@cs.stanford.edu")
    assert is_bodner_supp is False, "Abigail Bodner must not be classified as replied/suppressed"
    assert is_liang_supp is False, "Percy Liang must not be classified as replied/suppressed"


def test_queue_persistence_and_sequence_ordering(queue_test_env):
    """Verify that unsuppressed professors (including Bodner) are enqueued with exact subjects and preserved order."""
    repo, runner, _ = queue_test_env

    # Insert 3 professors (2 normal NZ, 1 normal MIT Bodner, 1 replied TUM Han Fan)
    repo.upsert_professor(Professor(
        professor_id="prof_nz_01",
        university_name="The University of Auckland",
        country="New Zealand",
        full_name="Brent Young",
        email="b.young@auckland.ac.nz",
        research_areas="Process systems engineering",
        time_block="BLOCK_01",
        time_block_order=1
    ))
    repo.upsert_professor(Professor(
        professor_id="prof_nz_02",
        university_name="The University of Auckland",
        country="New Zealand",
        full_name="Richard Clarke",
        email="rj.clarke@auckland.ac.nz",
        research_areas="Computational fluid dynamics",
        time_block="BLOCK_01",
        time_block_order=2
    ))
    repo.upsert_professor(Professor(
        professor_id="prof_mit_bodner",
        university_name="MIT",
        country="United States",
        full_name="Abigail Bodner",
        email="bodner@mit.edu",
        research_areas="Climate dynamics & SciML",
        time_block="BLOCK_10",
        time_block_order=920
    ))
    repo.upsert_professor(Professor(
        professor_id="prof_supp_tum_fan",
        university_name="Technical University of Munich",
        country="Germany",
        full_name="Han Fan",
        email="han.fan@tum.de",
        research_areas="Robotics",
        time_block="BLOCK_07",
        time_block_order=500
    ))

    build_res = runner.build_queue_from_professors(repo)
    assert build_res["enqueued_count"] == 3  # Brent Young, Richard Clarke, Abigail Bodner
    
    # Han Fan is already marked SUPPRESSED in professors table
    han_prof = repo.get_professor("prof_supp_tum_fan")
    assert han_prof["outreach_status"] == "SUPPRESSED"

    items = repo.list_queue_items()
    assert len(items) == 3
    assert items[0]["professor_name"] == "Brent Young"
    assert items[0]["sequence_order"] == 1
    assert items[0]["draft_subject"].startswith("Summer 2027 Research Internship — ")
    assert items[0]["draft_subject"].endswith(" | IIT Delhi")

    assert items[1]["professor_name"] == "Richard Clarke"
    assert items[1]["sequence_order"] == 2

    assert items[2]["professor_name"] == "Abigail Bodner"
    assert items[2]["recipient_email"] == "bodner@mit.edu"


def test_window_evaluation_90_minutes(queue_test_env):
    """Verify 90-minute window evaluation in IST."""
    _, runner, _ = queue_test_env

    ist_tz = ZoneInfo("Asia/Kolkata")
    # BLOCK_01 window is 01:00 to 02:30 IST
    # 1. Inside window: 01:45 IST
    dt_inside = datetime(2027, 2, 1, 1, 45, 0, tzinfo=ist_tz)
    is_active, _ = runner.is_window_active("BLOCK_01", dt_inside)
    assert is_active is True

    # 2. Outside window before: 00:50 IST
    dt_before = datetime(2027, 2, 1, 0, 50, 0, tzinfo=ist_tz)
    is_active, _ = runner.is_window_active("BLOCK_01", dt_before)
    assert is_active is False

    # 3. Outside window after: 02:35 IST
    dt_after = datetime(2027, 2, 1, 2, 35, 0, tzinfo=ist_tz)
    is_active, _ = runner.is_window_active("BLOCK_01", dt_after)
    assert is_active is False


def test_cadence_batching_and_cooling_break(queue_test_env):
    """Verify 5-message cadence batching and cooling breaks."""
    repo, runner, _ = queue_test_env

    # Seed 7 professors in BLOCK_01
    for i in range(1, 8):
        repo.upsert_professor(Professor(
            professor_id=f"prof_nz_{i:02d}",
            university_name="The University of Auckland",
            country="New Zealand",
            full_name=f"Professor {i}",
            email=f"prof_{i}@auckland.ac.nz",
            research_areas="Computational modeling",
            time_block="BLOCK_01",
            time_block_order=i
        ))

    runner.build_queue_from_professors(repo)
    items = repo.list_queue_items()
    assert len(items) == 7

    # Verify batch numbering: items 1..5 in batch 1, items 6..7 in batch 2
    assert items[0]["batch_number"] == 1
    assert items[4]["batch_number"] == 1
    assert items[5]["batch_number"] == 2
    assert items[6]["batch_number"] == 2

    # Dispatch batch in dry run with allow_templates=True
    dispatch_res = runner.dispatch_queue(
        repo=repo,
        time_block="BLOCK_01",
        max_messages=7,
        dry_run=True,
        force_window=True,
        simulated_cooling=True,
        allow_templates=True
    )

    assert dispatch_res["dispatched_count"] == 7
    assert dispatch_res["dry_run"] is True


def test_crash_recovery_mechanisms(queue_test_env):
    """
    Verify crash recovery prevents duplicate sending via AMBIGUOUS_DELIVERY state.
    Process crashes after SMTP accepts but before SQLite commits must require reconciliation.
    """
    repo, runner, _ = queue_test_env

    # Seed 2 professors and build queue
    for i in [1, 2]:
        repo.upsert_professor(Professor(
            professor_id=f"prof_crash_{i}",
            university_name="MIT",
            country="United States",
            full_name=f"Prof Crash {i}",
            email=f"crash_{i}@example.com",
            research_areas="Systems",
            time_block="BLOCK_10",
            time_block_order=i
        ))

    runner.build_queue_from_professors(repo)
    items = repo.list_queue_items()
    assert len(items) == 2

    q1_id = items[0]["queue_id"]
    q2_id = items[1]["queue_id"]

    # Simulate abrupt process crash: both items marked IN_PROGRESS
    repo.update_queue_status(q1_id, "IN_PROGRESS")
    repo.update_queue_status(q2_id, "IN_PROGRESS")

    # For q1, simulate that the email actually got dispatched before the crash
    repo.save_email_message(EmailMessage(
        message_id="msg_recovered_01",
        thread_id="th_recovered_01",
        professor_id="prof_crash_1",
        direction="OUTBOUND",
        sender="ch1251635@iitd.ac.in",
        recipient="crash_1@example.com",
        subject="Summer 2027 Research",
        body="Test body",
        sent_received_at=datetime.utcnow().isoformat()
    ))

    # For q2, no message exists in email_messages (crashed before send)

    # Trigger recovery: both items resolved
    recovered_count = repo.recover_crashed_queue_items()
    assert recovered_count == 2

    # Check statuses: q1 marked SENT, q2 marked AMBIGUOUS_DELIVERY (not blindly reset to PENDING!)
    cur = repo.conn.cursor()
    s1 = cur.execute("SELECT status FROM outreach_queue WHERE queue_id = ?;", (q1_id,)).fetchone()[0]
    s2 = cur.execute("SELECT status, error_message FROM outreach_queue WHERE queue_id = ?;", (q2_id,)).fetchone()

    assert s1 == "SENT"
    assert s2[0] == "AMBIGUOUS_DELIVERY"
    assert "Requires mail server reconciliation before retry" in s2[1]

    # Reconcile q2 after verifying with server that no email was transmitted
    repo.reconcile_ambiguous_delivery(q2_id, verified_sent=False, notes="Verified not in sent folder")
    s2_after = cur.execute("SELECT status FROM outreach_queue WHERE queue_id = ?;", (q2_id,)).fetchone()[0]
    assert s2_after == "PENDING"


def test_duplicate_outreach_prevention(queue_test_env):
    """Verify that already sent professors cannot be duplicate-dispatched."""
    repo, runner, _ = queue_test_env

    repo.upsert_professor(Professor(
        professor_id="prof_dup_01",
        university_name="Oxford",
        country="United Kingdom",
        full_name="Professor Duplicate",
        email="dup@ox.ac.uk",
        research_areas="Algorithms",
        time_block="BLOCK_08",
        time_block_order=1
    ))

    # Build queue and dispatch once
    runner.build_queue_from_professors(repo)
    res1 = runner.dispatch_queue(repo, time_block="BLOCK_08", max_messages=1, dry_run=True, force_window=True, allow_templates=True)
    assert res1["dispatched_count"] == 1

    # Attempt second dispatch: must find 0 pending items
    res2 = runner.dispatch_queue(repo, time_block="BLOCK_08", max_messages=1, dry_run=True, force_window=True, allow_templates=True)
    assert res2["dispatched_count"] == 0


def test_non_ai_draft_blocked_by_default(queue_test_env):
    """Verify that by default, non-AI or template drafts are blocked with HELD_NON_AI_DRAFT."""
    repo, runner, _ = queue_test_env

    repo.upsert_professor(Professor(
        professor_id="prof_block_01",
        university_name="Cambridge",
        country="United Kingdom",
        full_name="Prof Template",
        email="temp@cam.ac.uk",
        research_areas="Theoretical CS",
        time_block="BLOCK_08",
        time_block_order=1
    ))

    # Enqueue deterministic template draft
    runner.build_queue_from_professors(repo)

    # Attempt dispatch with default allow_templates=False
    res = runner.dispatch_queue(repo, time_block="BLOCK_08", max_messages=1, dry_run=True, force_window=True)
    assert res["dispatched_count"] == 0

    # Queue item should be marked HELD_NON_AI_DRAFT
    cur = repo.conn.cursor()
    row = cur.execute("SELECT status, error_message FROM outreach_queue WHERE professor_id = 'prof_block_01';").fetchone()
    assert row["status"] == "HELD_NON_AI_DRAFT"
    assert "Disallowing non-AI" in row["error_message"]
