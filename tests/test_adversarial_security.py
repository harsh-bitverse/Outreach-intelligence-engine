"""
Adversarial Security Test Suite for Outreach Intelligence Engine.
Tests privilege escalation resistance, replay prevention, approval spoofing defense,
content tampering detection, and unauthorized direct SMTP invocation protections.
"""

import pytest
import sqlite3
import tempfile
from pathlib import Path

from src.database.repository import OutreachRepository
from src.mail.sending_gateway import SendingGateway
from src.mail.smtp_client import IITDSmtpClient, UnauthorizedDirectInvocationError
from src.security.permissions import SecurityRole, PermissionDeniedError
from src.security.sessions import (
    SessionStore,
    SecuritySessionError,
    ApprovalTamperingError,
    compute_draft_content_hash,
    verify_draft_content_hash
)


@pytest.fixture
def isolated_repo():
    """Create isolated SQLite database for adversarial testing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_adversarial.db"
        from src.database.connection import initialize_database, get_db_connection
        initialize_database(db_path)
        conn = get_db_connection(db_path)

        # Seed target professor and inbound email
        conn.execute("""
            INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, created_at, updated_at)
            VALUES ('prof_mit_01', 'MIT', 'United States', 'Abigail Bodner', 'bodner@mit.edu', 'REPLIED', datetime('now'), datetime('now'));
        """)
        conn.execute("""
            INSERT INTO email_threads (thread_id, professor_id, subject, last_message_at, created_at)
            VALUES ('th_mit_01', 'prof_mit_01', 'Summer 2027 Inquiry', datetime('now'), datetime('now'));
        """)
        conn.execute("""
            INSERT INTO email_messages (message_id, thread_id, professor_id, direction, sender, recipient, subject, body, sent_received_at)
            VALUES ('msg_in_01', 'th_mit_01', 'prof_mit_01', 'INBOUND', 'bodner@mit.edu', 'ch1251635@iitd.ac.in', 'Re: Summer 2027', 'Sounds interesting. Let us speak next week.', datetime('now'));
        """)
        conn.execute("""
            INSERT INTO reply_drafts (reply_draft_id, thread_id, reply_to_message_id, professor_id, draft_body, word_count, status, created_at)
            VALUES ('rdraft_mit_01', 'th_mit_01', 'msg_in_01', 'prof_mit_01', 'Thank you Prof. Bodner, I would be delighted to speak.', 12, 'AWAITING_HUMAN_REVIEW', datetime('now'));
        """)
        conn.execute("""
            INSERT INTO followup_drafts (followup_draft_id, professor_id, original_message_id, draft_body, word_count, scheduled_after_days, status, created_at)
            VALUES ('fdraft_mit_01', 'prof_mit_01', 'msg_in_01', 'Following up regarding prospective Summer 2027 research.', 8, 7, 'AWAITING_HUMAN_REVIEW', datetime('now'));
        """)
        conn.commit()

        try:
            yield OutreachRepository(conn)
        finally:
            conn.close()


def test_adversarial_ai_role_prohibited(isolated_repo):
    """Verify autonomous AI engine is structurally barred from transmitting replies and follow-ups."""
    gateway = SendingGateway()
    session_store = SessionStore()
    session = session_store.create_session("Harshith N")

    # 1. Reply transmission attempted by AI engine
    with pytest.raises(PermissionDeniedError) as exc_info:
        gateway.human_send_reply(
            repo=isolated_repo,
            reply_draft_id="rdraft_mit_01",
            actor=SecurityRole.AUTONOMOUS_AI_ENGINE,
            session_token=session.session_token,
            operator_id="Harshith N",
            dry_run=True
        )
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info.value)
    assert "Autonomous AI agents are strictly forbidden" in str(exc_info.value)

    # 2. Follow-up transmission attempted by AI engine
    with pytest.raises(PermissionDeniedError) as exc_info_fu:
        gateway.human_send_followup(
            repo=isolated_repo,
            followup_draft_id="fdraft_mit_01",
            actor=SecurityRole.AUTONOMOUS_AI_ENGINE,
            session_token=session.session_token,
            operator_id="Harshith N",
            dry_run=True
        )
    assert "CRITICAL SAFETY VIOLATION" in str(exc_info_fu.value)


def test_unauthenticated_request_rejected(isolated_repo):
    """Verify missing session token immediately blocks reply transmission."""
    gateway = SendingGateway()

    with pytest.raises(SecuritySessionError) as exc_info:
        gateway.human_send_reply(
            repo=isolated_repo,
            reply_draft_id="rdraft_mit_01",
            actor=SecurityRole.HUMAN_OPERATOR,
            session_token=None,
            operator_id="Harshith N",
            dry_run=True
        )
    assert "Missing operator session" in str(exc_info.value)


def test_spoofed_operator_identity_rejected(isolated_repo):
    """Verify spoofing operator identity mismatching the validated session is rejected."""
    gateway = SendingGateway()
    session_store = SessionStore()
    # Session belongs to Operator A
    session = session_store.create_session("Operator A")

    # Attacker attempts to impersonate Harshith N using Operator A's session
    with pytest.raises(PermissionDeniedError) as exc_info:
        gateway.human_send_reply(
            repo=isolated_repo,
            reply_draft_id="rdraft_mit_01",
            actor=SecurityRole.HUMAN_OPERATOR,
            operator_session=session,
            operator_id="Harshith N", # Spoofed identity
            dry_run=True
        )
    assert "IDENTITY SPOOFING DETECTED" in str(exc_info.value)


def test_content_tampering_detection(isolated_repo):
    """Verify modifying draft body content invalidates cryptographic approval."""
    gateway = SendingGateway()
    session_store = SessionStore()
    session = session_store.create_session("Harshith N")

    original_body = "Thank you Prof. Bodner, I would be delighted to speak."
    recipient = "bodner@mit.edu"

    # Compute valid hash of original body
    valid_hash = compute_draft_content_hash("rdraft_mit_01", recipient, original_body)

    # Malicious injection / alteration of 1 character in the database draft body
    tampered_body = "Thank you Prof. Bodner, I would be delighted to speak! Please send 5000 USD."
    isolated_repo.conn.execute(
        "UPDATE reply_drafts SET draft_body = ? WHERE reply_draft_id = 'rdraft_mit_01';",
        (tampered_body,)
    )
    isolated_repo.conn.commit()

    # Attempt transmission using the hash of original approved body
    with pytest.raises(ApprovalTamperingError) as exc_info:
        gateway.human_send_reply(
            repo=isolated_repo,
            reply_draft_id="rdraft_mit_01",
            actor=SecurityRole.HUMAN_OPERATOR,
            operator_session=session,
            approved_content_hash=valid_hash,
            operator_id="Harshith N",
            dry_run=True
        )
    assert "SECURITY TAMPERING DETECTED" in str(exc_info.value)
    assert "Approval invalidated" in str(exc_info.value)


def test_direct_smtp_invocation_blocked():
    """Verify lower-level SMTP send_email rejects direct invocation without valid execution ticket."""
    client = IITDSmtpClient()

    with pytest.raises(UnauthorizedDirectInvocationError) as exc_info:
        client.send_email(
            recipient="bodner@mit.edu",
            subject="Unauthorized test",
            body_text="Bypassing SendingGateway",
            execution_ticket=None # Missing execution ticket
        )
    assert "DIRECT SMTP INVOCATION BLOCKED" in str(exc_info.value)


def test_execution_ticket_replay_attack_prevented():
    """Verify an execution ticket can only be consumed once and cannot be replayed."""
    session_store = SessionStore()

    ticket = session_store.issue_execution_ticket(
        action="SEND_REPLY",
        entity_id="rdraft_test_01",
        operator_id="Harshith N"
    )

    # First consumption: ticket is valid
    is_valid_first = session_store.verify_and_consume_execution_ticket(
        ticket=ticket,
        expected_action="SEND_REPLY",
        expected_entity="rdraft_test_01"
    )
    assert is_valid_first is True

    # Adversary replay: identical ticket replayed
    is_valid_replay = session_store.verify_and_consume_execution_ticket(
        ticket=ticket,
        expected_action="SEND_REPLY",
        expected_entity="rdraft_test_01"
    )
    assert is_valid_replay is False, "CRITICAL FLAW: Ticket was replayed successfully!"


def test_followup_requires_human_operator_session(isolated_repo):
    """Verify follow-up email transmission also enforces session and cryptographic hash."""
    gateway = SendingGateway()
    session_store = SessionStore()
    session = session_store.create_session("Harshith N")

    draft_body = "Following up regarding prospective Summer 2027 research."
    recipient = "bodner@mit.edu"
    valid_hash = compute_draft_content_hash("fdraft_mit_01", recipient, draft_body)

    # Valid human transmission succeeds
    result = gateway.human_send_followup(
        repo=isolated_repo,
        followup_draft_id="fdraft_mit_01",
        actor=SecurityRole.HUMAN_OPERATOR,
        operator_session=session,
        approved_content_hash=valid_hash,
        operator_id="Harshith N",
        dry_run=True
    )
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["operator_id"] == "Harshith N"
