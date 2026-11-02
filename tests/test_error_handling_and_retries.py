"""
Unit and integration tests for robust error handling, bounded exponential backoff,
maximum-three-attempt retry policy, and account-wide rate-limit cooldowns.
"""

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import openpyxl
import pytest

from config.settings import settings
from src.database.connection import get_db
from src.database.repository import compute_durable_professor_id
from src.database.workbook_sync import DynamicWorkbookSync
from src.mail.smtp_client import (
    SMTPPermanentError,
    SMTPTransientError,
    SMTPRateLimitError,
    SMTPAmbiguousDeliveryError
)
from src.scheduler.autonomous_campaign_loop import (
    AutonomousCampaignLoop,
    CLIQuotaExhaustionError,
    CLIDraftGenerationError,
    DraftValidationError
)


@pytest.fixture
def temp_workbook():
    """Create an isolated copy of the authoritative workbook for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "test_prof_db.xlsx"
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, tmp_path)
        yield tmp_path


@pytest.fixture
def temp_db(monkeypatch):
    """Create an isolated copy of the SQLite database so tests never taint production DB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db_path = Path(tmpdir) / "test_outreach_engine.db"
        shutil.copy2(settings.SQLITE_DB_PATH, tmp_db_path)
        monkeypatch.setattr(settings, "SQLITE_DB_PATH", tmp_db_path)
        yield tmp_db_path


@pytest.fixture
def isolated_loop(temp_workbook, temp_db):
    """Instantiate AutonomousCampaignLoop with an isolated workbook and DynamicWorkbookSync."""
    sync = DynamicWorkbookSync(workbook_path=temp_workbook)
    loop = AutonomousCampaignLoop(
        workbook_path=temp_workbook,
        sync=sync,
        cli_drafter=MagicMock()
    )
    return loop


# ------------------------------------------------------------------------------
# Test 1: Maximum 3 attempts with exponential backoff on retryable transient error
# ------------------------------------------------------------------------------
def test_transient_smtp_error_exhaustion_marks_error(isolated_loop, temp_workbook):
    """
    Verify that retryable transient SMTP errors are attempted exactly 3 times
    with bounded backoff, and on the 3rd failure, the professor is marked ERROR
    in SQLite and the SAME Time Blocks & Distribution Excel row.
    """
    candidate = {
        "full_name": "Test Transient Faculty",
        "title": "Professor",
        "email": "test.transient@example.edu",
        "university": "Transient State University",
        "research_areas": "Distributed Systems",
        "research_overlap": "Distributed consensus",
        "cv_skill_project": "Systems profiling",
        "potential_contribution": "Model parallel diagnostics",
        "block_id": "BLOCK_06",
        "row_index": 50,
        "iana_timezone": "Europe/London"
    }

    # Mock candidate discovery to return this candidate, then empty
    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    # Mock dispatch_and_persist to fail with transient error
    dispatch_mock = MagicMock(side_effect=SMTPTransientError("421 4.4.2 Temporary network connection dropped"))
    isolated_loop.dispatch_and_persist = dispatch_mock

    # Run continuous campaign in test mode with sleep mocked
    with patch("time.sleep") as mock_sleep:
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=False,
            stop_on_idle=True
        )

    # Verify dispatch was attempted exactly 3 times
    assert dispatch_mock.call_count == 3

    # Verify backoff sleep calls: attempt 1 -> 8s, attempt 2 -> 16s
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert 8 in sleep_calls
    assert 16 in sleep_calls

    # Verify SQLite status is ERROR
    prof_id = compute_durable_professor_id(candidate["university"], candidate["full_name"])
    with get_db() as conn:
        p_row = conn.execute("SELECT outreach_status, notes FROM professors WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert p_row is not None
        assert p_row["outreach_status"] == "ERROR"
        assert "TRANSIENT_SMTP_EXHAUSTED" in p_row["notes"]
        assert "Attempts:3" in p_row["notes"]

        q_row = conn.execute("SELECT status, attempts, error_message FROM outreach_queue WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert q_row is not None
        assert q_row["status"] == "ERROR"
        assert q_row["attempts"] == 3


# ------------------------------------------------------------------------------
# Test 2: Permanent recipient errors mark ERROR immediately without retries
# ------------------------------------------------------------------------------
def test_permanent_smtp_error_marks_error_immediately(isolated_loop, temp_workbook):
    """
    Verify that permanent recipient errors (e.g. 550 User unknown) immediately
    mark ERROR with 1 attempt and zero unnecessary retries.
    """
    candidate = {
        "full_name": "Test Invalid Recipient",
        "title": "Professor",
        "email": "invalid.address.550@example.edu",
        "university": "Nonexistent University",
        "research_areas": "Static Analysis",
        "research_overlap": "Compiler optimizations",
        "cv_skill_project": "Surrogate modeling",
        "potential_contribution": "Code generation benchmarks",
        "block_id": "BLOCK_06",
        "row_index": 51,
        "iana_timezone": "Europe/London"
    }

    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    dispatch_mock = MagicMock(side_effect=SMTPPermanentError("550 5.1.1 Recipient address rejected: User unknown"))
    isolated_loop.dispatch_and_persist = dispatch_mock

    with patch("time.sleep"):
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=False,
            stop_on_idle=True
        )

    # Dispatched exactly ONCE without retrying
    assert dispatch_mock.call_count == 1

    prof_id = compute_durable_professor_id(candidate["university"], candidate["full_name"])
    with get_db() as conn:
        p_row = conn.execute("SELECT outreach_status, notes FROM professors WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert p_row is not None
        assert p_row["outreach_status"] == "ERROR"
        assert "PERMANENT_SMTP_REJECTION" in p_row["notes"]
        assert "Attempts:1" in p_row["notes"]


# ------------------------------------------------------------------------------
# Test 3: IITD SMTP rate-limit (450 4.7.1) triggers account-wide cooldown
# ------------------------------------------------------------------------------
def test_smtp_rate_limit_cooldown_pauses_account_wide(isolated_loop):
    """
    Verify that 450 4.7.1 rate-limit pauses sending account-wide for cooldown,
    does NOT burn the candidate's retry attempts, does NOT advance to other candidates,
    and resumes the same professor after cooldown.
    """
    candidate = {
        "full_name": "Rate Limited Professor",
        "title": "Professor",
        "email": "ratelimit.prof@example.edu",
        "university": "Rate Limited Institute",
        "research_areas": "Numerical Algorithms",
        "research_overlap": "Finite difference methods",
        "cv_skill_project": "Neural Process Twin",
        "potential_contribution": "PDE solver accelerating",
        "block_id": "BLOCK_06",
        "row_index": 52,
        "iana_timezone": "Europe/London"
    }

    # Candidate returned on first pass, then empty once dispatched
    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    # First dispatch raises 450 rate limit; second dispatch succeeds
    successful_result = {
        "success": True,
        "professor_name": candidate["full_name"],
        "email": candidate["email"],
        "university": candidate["university"],
        "block_id": candidate["block_id"],
        "subject": "Summer 2027 Research Internship — Numerical Algorithms | IIT Delhi",
        "message_id": "<simulated_rate_limit_recovered@iitd.ac.in>",
        "status": "SIMULATED_SENT",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "excel_updated": True,
        "dry_run": True
    }
    dispatch_mock = MagicMock(side_effect=[
        SMTPRateLimitError("450 4.7.1 Policy Rejection- Sending rate too high!"),
        successful_result
    ])
    isolated_loop.dispatch_and_persist = dispatch_mock

    with patch("time.sleep") as mock_sleep:
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=False,
            stop_on_idle=True
        )

    # Rate limit cooldown enforced (900 seconds)
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert settings.SMTP_RATE_LIMIT_COOLDOWN_SECONDS in sleep_calls

    # Second attempt succeeded on the SAME recipient
    assert dispatch_mock.call_count == 2
    assert dispatch_mock.call_args_list[0].kwargs["prof_row"]["email"] == candidate["email"]
    assert dispatch_mock.call_args_list[1].kwargs["prof_row"]["email"] == candidate["email"]


# ------------------------------------------------------------------------------
# Test 4: Ambiguous delivery outcome marked AMBIGUOUS_DELIVERY and never resent
# ------------------------------------------------------------------------------
def test_ambiguous_delivery_marks_ambiguous_and_never_resends(isolated_loop, temp_workbook):
    """
    Verify that connection timeouts during or after DATA command transmission
    are marked AMBIGUOUS_DELIVERY in SQLite and Excel, and automatic retransmission
    is strictly prohibited.
    """
    candidate = {
        "full_name": "Ambiguous Delivery Faculty",
        "title": "Professor",
        "email": "ambiguous.delivery@example.edu",
        "university": "Network Drop University",
        "research_areas": "High Performance Computing",
        "research_overlap": "GPU kernel profiling",
        "cv_skill_project": "LLM Inference Engine",
        "potential_contribution": "Roofline modeling",
        "block_id": "BLOCK_06",
        "row_index": 53,
        "iana_timezone": "Europe/London"
    }

    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    dispatch_mock = MagicMock(side_effect=SMTPAmbiguousDeliveryError("Connection reset by peer after DATA block"))
    isolated_loop.dispatch_and_persist = dispatch_mock

    with patch("time.sleep"):
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=False,
            stop_on_idle=True
        )

    # Dispatched once, never retried automatically
    assert dispatch_mock.call_count == 1

    prof_id = compute_durable_professor_id(candidate["university"], candidate["full_name"])
    with get_db() as conn:
        p_row = conn.execute("SELECT outreach_status, notes FROM professors WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert p_row is not None
        assert p_row["outreach_status"] == "AMBIGUOUS_DELIVERY"
        assert "MANUAL_RECONCILIATION_REQUIRED" in p_row["notes"]

        q_row = conn.execute("SELECT status, error_message FROM outreach_queue WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert q_row is not None
        assert q_row["status"] == "AMBIGUOUS_DELIVERY"


# ------------------------------------------------------------------------------
# Test 5: Excel update failure after SMTP transmission preserves SENT in SQLite
# ------------------------------------------------------------------------------
def test_excel_sync_failure_preserves_sent_without_retransmission(isolated_loop):
    """
    Verify that if SMTP transmission succeeds but Excel workbook update fails
    after retries, SQLite preserves SENT state with EXCEL_SYNC_PENDING note,
    and the email is NEVER retransmitted.
    """
    candidate = {
        "full_name": "Excel Failure Professor",
        "title": "Professor",
        "email": "excel.fail@example.edu",
        "university": "Locked Workbook University",
        "research_areas": "Scientific AI",
        "research_overlap": "Surrogate models",
        "cv_skill_project": "Neural Process Twin",
        "potential_contribution": "Physics-informed neural networks",
        "block_id": "BLOCK_06",
        "row_index": 54,
        "iana_timezone": "Europe/London"
    }

    draft = isolated_loop.generate_ai_draft(candidate, prefer_cli=False)

    # Active block mock so live dispatch doesn't fail with TIME_BLOCK_EXPIRED
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))

    # Mock SMTP to succeed
    smtp_mock = MagicMock(return_value={
        "status": "SENT",
        "message_id": "<simulated_excel_lock_msg@iitd.ac.in>",
        "accepted": [candidate["email"]],
        "rejected": {}
    })
    isolated_loop.smtp.send_email = smtp_mock

    # Mock Excel sync to fail on all retries
    isolated_loop.sync.update_time_blocks_row = MagicMock(side_effect=PermissionError("Excel file locked by external process"))

    with patch("time.sleep"):
        res = isolated_loop.dispatch_and_persist(
            prof_row=candidate,
            draft=draft,
            dry_run=False,
            actor="TEST_EXCEL_SYNC_FAILURE"
        )

    # SMTP send_email called exactly once
    assert smtp_mock.call_count == 1
    assert res["status"] == "SENT"
    assert res["excel_updated"] is False

    # Check SQLite status is SENT with EXCEL_SYNC_PENDING
    prof_id = compute_durable_professor_id(candidate["university"], candidate["full_name"])
    with get_db() as conn:
        p_row = conn.execute("SELECT outreach_status, notes FROM professors WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert p_row is not None
        assert p_row["outreach_status"] == "SENT"
        assert "EXCEL_SYNC_PENDING" in p_row["notes"]

        q_row = conn.execute("SELECT status FROM outreach_queue WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert q_row is not None
        assert q_row["status"] == "SENT"


# ------------------------------------------------------------------------------
# Test 6: Antigravity CLI quota exhaustion triggers account-wide cooldown
# ------------------------------------------------------------------------------
def test_antigravity_cli_quota_exhaustion_cooldown(isolated_loop):
    """
    Verify that Antigravity CLI RESOURCE_EXHAUSTED / 429 quota exhaustion
    triggers provider-wide cooldown without exhausting the candidate's attempt count.
    """
    candidate = {
        "full_name": "CLI Quota Professor",
        "title": "Professor",
        "email": "cli.quota@example.edu",
        "university": "AI Institute",
        "research_areas": "Machine Learning",
        "research_overlap": "Deep learning architectures",
        "cv_skill_project": "LLM Inference Engine",
        "potential_contribution": "Transformer benchmarking",
        "block_id": "BLOCK_06",
        "row_index": 55,
        "iana_timezone": "Europe/London"
    }

    # Candidate returned on first pass, then empty once dispatched
    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    valid_draft = isolated_loop.generate_ai_draft(candidate, prefer_cli=False)

    # First call: quota error; second call: valid draft
    generate_mock = MagicMock(side_effect=[
        CLIQuotaExhaustionError("429 RESOURCE_EXHAUSTED: Rate limit reached for agy process"),
        valid_draft
    ])
    isolated_loop.generate_ai_draft = generate_mock
    isolated_loop.dispatch_and_persist = MagicMock(return_value={"status": "SIMULATED_SENT", "message_id": "<msg@iitd.ac.in>", "email": candidate["email"]})

    with patch("time.sleep") as mock_sleep:
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=True,
            stop_on_idle=True
        )

    # Quota cooldown enforced
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert settings.CLI_QUOTA_COOLDOWN_SECONDS in sleep_calls

    # Draft generator resumed same candidate
    assert generate_mock.call_count == 2


# ------------------------------------------------------------------------------
# Test 7: CLI drafting failure retries 3 times before recording ERROR
# ------------------------------------------------------------------------------
def test_cli_drafting_failure_retries_and_records_error(isolated_loop, temp_workbook):
    """
    Verify that AI drafting/validation failures retry up to 3 times
    with exponential backoff, then record ERROR in SQLite and Excel.
    """
    candidate = {
        "full_name": "CLI Malformed Professor",
        "title": "Professor",
        "email": "cli.malformed@example.edu",
        "university": "Syntax Error University",
        "research_areas": "Theoretical Physics",
        "research_overlap": "Quantum simulations",
        "cv_skill_project": "Neural Process Twin",
        "potential_contribution": "Hamiltonian modeling",
        "block_id": "BLOCK_06",
        "row_index": 56,
        "iana_timezone": "Europe/London"
    }

    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    generate_mock = MagicMock(side_effect=CLIDraftGenerationError("CLI returned malformed non-JSON output"))
    isolated_loop.generate_ai_draft = generate_mock

    with patch("time.sleep") as mock_sleep:
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=True,
            stop_on_idle=True
        )

    # Attempted exactly 3 times
    assert generate_mock.call_count == 3

    # Backoff sleeps occurred
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert 8 in sleep_calls
    assert 16 in sleep_calls

    # Status marked ERROR
    prof_id = compute_durable_professor_id(candidate["university"], candidate["full_name"])
    with get_db() as conn:
        p_row = conn.execute("SELECT outreach_status, notes FROM professors WHERE professor_id = ?;", (prof_id,)).fetchone()
        assert p_row is not None
        assert p_row["outreach_status"] == "ERROR"
        assert "AI_DRAFTING_EXHAUSTED" in p_row["notes"]
        assert "Attempts:3" in p_row["notes"]


# ------------------------------------------------------------------------------
# Test 8: Failed professor does not block subsequent candidates in the active block
# ------------------------------------------------------------------------------
def test_failed_professor_does_not_block_subsequent_candidates(isolated_loop):
    """
    Verify that when candidate 1 fails with a terminal error, the loop
    advances immediately to candidate 2 in the same active block.
    """
    candidate_1 = {
        "full_name": "Failed Professor One",
        "title": "Professor",
        "email": "failed.one@example.edu",
        "university": "First University",
        "research_areas": "Biophysics",
        "research_overlap": "Molecular dynamics",
        "cv_skill_project": "Neural Process Twin",
        "potential_contribution": "Solvation modeling",
        "block_id": "BLOCK_06",
        "row_index": 57,
        "iana_timezone": "Europe/London"
    }

    candidate_2 = {
        "full_name": "Successful Professor Two",
        "title": "Professor",
        "email": "success.two@example.edu",
        "university": "Second University",
        "research_areas": "Chemical Reaction Engineering",
        "research_overlap": "Catalytic kinetics",
        "cv_skill_project": "Neural Process Twin",
        "potential_contribution": "Microkinetic modeling",
        "block_id": "BLOCK_06",
        "row_index": 58,
        "iana_timezone": "Europe/London"
    }

    # Sequence of candidate discoveries: candidate 1 first, then candidate 2, then empty
    isolated_loop.find_eligible_professors_in_block = MagicMock(side_effect=[[candidate_1], [candidate_2], []])
    isolated_loop.get_active_block = MagicMock(return_value=("BLOCK_06", {"name": "Test", "start_ist": "11:00", "end_ist": "12:00"}))
    isolated_loop.is_local_working_day = MagicMock(return_value=(True, "Eligible"))

    def mock_dispatch(prof_row, draft, dry_run=True):
        if prof_row["email"] == candidate_1["email"]:
            raise SMTPPermanentError("550 User unknown")
        return {
            "success": True,
            "professor_name": prof_row["full_name"],
            "email": prof_row["email"],
            "university": prof_row["university"],
            "block_id": prof_row["block_id"],
            "subject": draft["subject"],
            "message_id": "<simulated_success_msg@iitd.ac.in>",
            "status": "SIMULATED_SENT",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "excel_updated": True,
            "dry_run": True
        }

    isolated_loop.dispatch_and_persist = MagicMock(side_effect=mock_dispatch)

    with patch("time.sleep"):
        isolated_loop.run_continuous_campaign(
            dry_run=True,
            prefer_cli=False,
            stop_on_idle=True
        )

    # Candidate 1 was marked ERROR
    p1_id = compute_durable_professor_id(candidate_1["university"], candidate_1["full_name"])
    with get_db() as conn:
        p1_row = conn.execute("SELECT outreach_status FROM professors WHERE professor_id = ?;", (p1_id,)).fetchone()
        assert p1_row is not None
        assert p1_row["outreach_status"] == "ERROR"

    # Candidate 2 was successfully dispatched
    assert isolated_loop.dispatch_and_persist.call_count == 2
    assert isolated_loop.dispatch_and_persist.call_args_list[1].kwargs["prof_row"]["email"] == candidate_2["email"]


# ------------------------------------------------------------------------------
# Test 9: Complete suppression of 37 previously sent professors
# ------------------------------------------------------------------------------
def test_suppression_of_37_previously_sent_professors(isolated_loop):
    """
    Verify that all 37 professors successfully contacted on Monday, November 2, 2026
    are suppressed by find_eligible_professors_in_block across BLOCK_01 and BLOCK_02,
    preventing any duplicate transmissions.
    """
    with get_db() as conn:
        sent_emails = {r[0].lower().strip() for r in conn.execute("SELECT email FROM professors WHERE outreach_status = 'SENT';").fetchall() if r[0]}

    assert len(sent_emails) == 37, f"Expected exactly 37 sent records, found {len(sent_emails)}"

    # BLOCK_01 candidates: all were sent, so 0 eligible candidates should be returned
    b1_candidates = isolated_loop.find_eligible_professors_in_block(block_id="BLOCK_01", limit=100)
    for c in b1_candidates:
        assert c["email"].lower() not in sent_emails, f"Candidate {c['email']} was already sent but returned by find_eligible_professors_in_block"

    # BLOCK_02 candidates: verified sent are suppressed; first eligible is Hanna Kurniawati (Row 48)
    b2_candidates = isolated_loop.find_eligible_professors_in_block(block_id="BLOCK_02", limit=10)
    assert len(b2_candidates) > 0
    assert b2_candidates[0]["full_name"] == "Hanna Kurniawati"
    for c in b2_candidates:
        assert c["email"].lower() not in sent_emails, f"Candidate {c['email']} was already sent but returned by find_eligible_professors_in_block"

