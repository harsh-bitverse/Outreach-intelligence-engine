"""
Unit Tests for Security Roles, Application-Level Permission Boundaries,
Suppression Enforcement, and Emergency Stop Killswitch.
"""

import tempfile
from pathlib import Path
import pytest
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.mail.sending_gateway import SendingGateway
from src.security.permissions import (
    SecurityRole, SecurityAction, require_permission,
    PermissionDeniedError, CampaignAuthorizationError, validate_campaign_preflight
)
from config.settings import settings


@pytest.fixture
def test_repo():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_perm.db"
        initialize_database(db_path)
        with get_db(db_path) as conn:
            repo = OutreachRepository(conn)
            yield repo


def test_autonomous_ai_prohibited_from_sending_replies(test_repo):
    """Verify that autonomous AI engine cannot invoke reply transmission."""
    gateway = SendingGateway()

    # Seed test professor and reply draft
    test_repo.conn.execute("""
        INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, created_at, updated_at)
        VALUES ('prof_001', 'Stanford', 'United States', 'Percy Liang', 'pliang@cs.stanford.edu', 'NEW', datetime('now'), datetime('now'));
    """)
    test_repo.conn.execute("""
        INSERT INTO email_threads (thread_id, professor_id, subject, last_message_at, created_at)
        VALUES ('th_001', 'prof_001', 'Outreach', datetime('now'), datetime('now'));
    """)
    test_repo.conn.execute("""
        INSERT INTO email_messages (message_id, thread_id, professor_id, direction, sender, recipient, subject, body, sent_received_at)
        VALUES ('msg_prof_reply', 'th_001', 'prof_001', 'INBOUND', 'pliang@cs.stanford.edu', 'ch1251635@iitd.ac.in', 'Re: Outreach', 'Sounds interesting', datetime('now'));
    """)
    test_repo.conn.execute("""
        INSERT INTO reply_drafts (reply_draft_id, thread_id, reply_to_message_id, professor_id, draft_body, word_count, status, created_at)
        VALUES ('rdraft_001', 'th_001', 'msg_prof_reply', 'prof_001', 'Draft content', 30, 'AWAITING_HUMAN_REVIEW', datetime('now'));
    """)

    # Attempting to call human_send_reply as AUTONOMOUS_AI_ENGINE must fail
    with pytest.raises(PermissionDeniedError) as exc_info:
        gateway.human_send_reply(
            repo=test_repo,
            reply_draft_id="rdraft_001",
            actor=SecurityRole.AUTONOMOUS_AI_ENGINE,
            operator_id=None,
            dry_run=True
        )
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info.value)
    assert "HUMAN_OPERATOR" in str(exc_info.value)


def test_human_operator_can_send_reply_in_dry_run(test_repo):
    """Verify that a verified human operator can execute reply transmission."""
    gateway = SendingGateway()

    # Seed professor and reply draft
    test_repo.conn.execute("""
        INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, created_at, updated_at)
        VALUES ('prof_002', 'Stanford', 'United States', 'Percy Liang', 'pliang@cs.stanford.edu', 'NEW', datetime('now'), datetime('now'));
    """)
    test_repo.conn.execute("""
        INSERT INTO email_threads (thread_id, professor_id, subject, last_message_at, created_at)
        VALUES ('th_002', 'prof_002', 'Outreach', datetime('now'), datetime('now'));
    """)
    test_repo.conn.execute("""
        INSERT INTO email_messages (message_id, thread_id, professor_id, direction, sender, recipient, subject, body, sent_received_at)
        VALUES ('msg_prof_002', 'th_002', 'prof_002', 'INBOUND', 'pliang@cs.stanford.edu', 'ch1251635@iitd.ac.in', 'Re: Outreach', 'Let us talk', datetime('now'));
    """)
    test_repo.conn.execute("""
        INSERT INTO reply_drafts (reply_draft_id, thread_id, reply_to_message_id, professor_id, draft_body, word_count, status, created_at)
        VALUES ('rdraft_002', 'th_002', 'msg_prof_002', 'prof_002', 'Human approved draft', 35, 'AWAITING_HUMAN_REVIEW', datetime('now'));
    """)

    result = gateway.human_send_reply(
        repo=test_repo,
        reply_draft_id="rdraft_002",
        actor=SecurityRole.HUMAN_OPERATOR,
        operator_id="Harshith N",
        dry_run=True
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["operator_id"] == "Harshith N"

    # Verify status in database
    cursor = test_repo.conn.execute("SELECT status, human_reviewer FROM reply_drafts WHERE reply_draft_id = 'rdraft_002';")
    row = cursor.fetchone()
    assert row["status"] == "HUMAN_SENT"
    assert row["human_reviewer"] == "Harshith N"


def test_permanent_suppressions_enforced(test_repo):
    """Verify that IIT Delhi, Kai-Kit Wong, and Chenshu Wu remain strictly suppressed."""
    # 1. Self institution check
    is_supp, reason = test_repo.is_suppressed("prof@iitd.ac.in")
    assert is_supp is True
    assert "INSTITUTIONAL_SELF" in reason

    is_supp, reason = test_repo.is_suppressed("hod@chemical.iitd.ac.in")
    assert is_supp is True
    assert "INSTITUTIONAL_SELF" in reason

    # 2. Previously contacted: Kai-Kit Wong
    is_supp, reason = test_repo.is_suppressed("k.wong@ucl.ac.uk")
    assert is_supp is True
    assert "Kai-Kit Wong" in reason or "PREVIOUSLY_CONTACTED" in reason

    # 3. Previously contacted: Chenshu Wu
    is_supp, reason = test_repo.is_suppressed("cwu@cs.hku.hk")
    assert is_supp is True
    assert "Chenshu Wu" in reason or "PREVIOUSLY_CONTACTED" in reason

    is_supp, reason = test_repo.is_suppressed("chenshu.wu@rutgers.edu")
    assert is_supp is True


def test_emergency_stop_killswitch_blocks_dispatch():
    """Verify that emergency stop halts preflight checks immediately."""
    with pytest.raises(CampaignAuthorizationError) as exc_info:
        validate_campaign_preflight(
            gateway_enabled=True,
            campaign_authorized=True,
            csc_approved=True,
            emergency_stop=True,
            dry_run=True
        )
    assert "Emergency Stop is currently ACTIVE" in str(exc_info.value)
