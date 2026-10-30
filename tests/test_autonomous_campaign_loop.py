"""
Unit tests for AutonomousCampaignLoop and Time-Blocks Excel Synchronization.
"""

import shutil
import tempfile
from datetime import datetime, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import openpyxl
import pytest

from config.settings import settings
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.scheduler.autonomous_campaign_loop import (
    AutonomousCampaignLoop,
    AntigravityCLIDrafter,
    CAMPAIGN_START_DATE
)


@pytest.fixture
def temp_workbook():
    """Create an isolated copy of the authoritative workbook for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "test_prof_db.xlsx"
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, tmp_path)
        yield tmp_path


@pytest.fixture
def test_loop(temp_workbook):
    """Instantiate AutonomousCampaignLoop pointing to the test workbook."""
    sync = DynamicWorkbookSync(workbook_path=temp_workbook)
    return AutonomousCampaignLoop(workbook_path=temp_workbook, sync=sync)


def test_campaign_start_date():
    """Verify authoritative campaign start date is Monday, November 2, 2026."""
    assert CAMPAIGN_START_DATE == date(2026, 11, 2)
    assert CAMPAIGN_START_DATE.weekday() == 0  # Monday


def test_is_local_working_day(test_loop):
    """Verify local working day check accepts Monday–Friday business hours and rejects weekends."""
    ist_tz = ZoneInfo("Asia/Kolkata")

    # Monday morning in Auckland (UTC+13)
    # 01:30 IST on Mon Nov 2, 2026 -> 09:00 NZDT on Mon Nov 2, 2026
    mon_dt = datetime(2026, 11, 2, 1, 30, tzinfo=ist_tz)
    is_workday, desc = test_loop.is_local_working_day("Pacific/Auckland", mon_dt)
    assert is_workday is True
    assert "Monday" in desc

    # Saturday in Auckland
    sat_dt = datetime(2026, 10, 31, 1, 30, tzinfo=ist_tz)
    is_workday, desc = test_loop.is_local_working_day("Pacific/Auckland", sat_dt)
    assert is_workday is False
    assert "Saturday" in desc

    # Midnight local time (outside 8 AM - 6 PM business hours)
    midnight_dt = datetime(2026, 11, 2, 16, 30, tzinfo=ist_tz)  # ~05:00 NZDT next morning
    is_workday, desc = test_loop.is_local_working_day("Pacific/Auckland", midnight_dt)
    assert is_workday is False


def test_find_eligible_professors_in_block(test_loop):
    """Verify direct traversal of Time Blocks & Distribution sheet finds candidates in order."""
    candidates = test_loop.find_eligible_professors_in_block(block_id="BLOCK_01", limit=7)
    assert len(candidates) >= 3

    # Check first candidate in sequence
    c1 = candidates[0]
    assert c1["full_name"] in ["Brent Young", "Richard Clarke"]
    assert c1["university"] == "The University of Auckland"
    assert c1["block_id"] == "BLOCK_01"
    assert len(c1["research_areas"]) > 0


def test_generate_ai_draft_and_validation(test_loop):
    """Verify genuine AI draft adheres to 130–180 word gate, 4 paragraphs, dual attachments, factual claims."""
    candidate = {
        "full_name": "Brent Young",
        "title": "Professor",
        "email": "b.young@auckland.ac.nz",
        "university": "The University of Auckland",
        "research_areas": "Process systems engineering, dynamic process simulation, digital twins",
        "research_overlap": "Synergy between faculty work in process systems engineering and digital twins",
        "cv_skill_project": "Chemical engineering coursework, surrogate modeling",
        "potential_contribution": "Developing high-throughput computational screening pipelines",
        "block_id": "BLOCK_01"
    }

    draft = test_loop.generate_ai_draft(candidate)

    # Subject check
    assert draft["subject"].startswith("Summer 2027 Research Internship — ")
    assert draft["subject"].endswith(" | IIT Delhi")

    # Word count check
    wc = draft["content_word_count"]
    assert 130 <= wc <= 180, f"Word count {wc} out of expected 130–180 range"

    # Factual grounding: no fabricated speedups or certified bound claims
    assert "speedup" not in draft["body"].lower()
    assert "certified error" not in draft["body"].lower()

    # Both attachments verified
    assert len(draft["attachments"]) == 2
    assert draft["attachments"][0][1] == "Harshith_N_Research_CV_Summer2027.pdf"
    assert draft["attachments"][1][1] == "Harshith_N_Research_Interests_Summer2027.pdf"

    # Validation pass
    is_valid, errors = test_loop.validate_draft(draft, candidate)
    assert is_valid is True, f"Validation failed: {errors}"


def test_update_time_blocks_row(temp_workbook):
    """Verify updating a row in Time Blocks & Distribution worksheet updates Columns 25, 29, 30, 31."""
    sync = DynamicWorkbookSync(workbook_path=temp_workbook)
    test_email = "b.young@auckland.ac.nz"
    now_iso = datetime.now(timezone.utc).isoformat()
    test_msg_id = "<test_msg_id_12345@iitd.ac.in>"

    success = sync.update_time_blocks_row(
        email=test_email,
        status="SENT",
        sent_at=now_iso,
        message_id=test_msg_id
    )
    assert success is True

    # Inspect sheet
    wb = openpyxl.load_workbook(str(temp_workbook))
    ws = wb["Time Blocks & Distribution"]

    # Row 8 is Brent Young
    assert ws.cell(8, 25).value == "SENT"
    assert ws.cell(8, 29).value == "SENT"
    assert ws.cell(8, 30).value == now_iso
    assert ws.cell(8, 31).value == test_msg_id

    # Verify headers created in Row 7
    assert ws.cell(7, 29).value == "Outreach Status"
    assert ws.cell(7, 30).value == "Sent Timestamp"
    assert ws.cell(7, 31).value == "Message-ID"
    wb.close()


def test_demonstration_sequence(test_loop):
    """
    Verify complete simulated sequence:
    1. Processes at least 3 professors in BLOCK_01.
    2. Updates Excel status and SQLite.
    3. Demonstrates automatic resumption after block transition to BLOCK_02.
    """
    res = test_loop.run_simulation_sequence(
        block_id="BLOCK_01",
        next_block_id="BLOCK_02",
        count=3,
        dry_run=True
    )

    assert res["initial_block"] == "BLOCK_01"
    assert len(res["professors_processed"]) >= 3

    for p in res["professors_processed"]:
        assert p["status"] == "SIMULATED_SENT"
        assert p["excel_updated"] is True
        assert p["message_id"].endswith("@iitd.ac.in>")

    # Block transition resumption verified
    assert res["block_transition_demonstrated"] is True
    assert res["resumed_block"] == "BLOCK_02"
    assert res["resumed_professor"] is not None
    assert res["resumed_professor"]["block_id"] == "BLOCK_02"
    assert res["resumed_professor"]["excel_updated"] is True


def test_antigravity_cli_drafter_discovery():
    """Verify AntigravityCLIDrafter locates official agy binary on Windows."""
    drafter = AntigravityCLIDrafter()
    assert drafter.is_available() is True
    assert drafter.cli_path.name.lower() in ("agy.exe", "agy")


def test_antigravity_sdk_authentication_limitation():
    """
    Verify documented architectural limitation:
    google-antigravity Python SDK requires a GEMINI_API_KEY and cannot
    directly authenticate via personal Google AI Pro OAuth keyring tokens.
    """
    import asyncio
    from google.antigravity import Agent, LocalAgentConfig
    from google.antigravity.types import AntigravityValidationError

    async def run_unauthenticated_agent():
        config = LocalAgentConfig(system_instructions="Test")
        async with Agent(config) as agent:
            await agent.chat("ping")

    with pytest.raises(AntigravityValidationError) as exc_info:
        asyncio.run(run_unauthenticated_agent())

    assert "A Gemini API key is required" in str(exc_info.value)

