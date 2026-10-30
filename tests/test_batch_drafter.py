"""
Tests for Scalable AI Batch Drafter & Persistent Generation Cursor Module.
Verifies cursor persistence, workbook order traversal, validation gates,
draft staging, and comprehensive progress metrics accounting.
"""

import json
import sqlite3
import pytest
from pathlib import Path

from config.settings import settings
from src.database.connection import SCHEMA_SQL, seed_mandatory_suppressions
from src.database.repository import OutreachRepository
from src.intelligence.batch_drafter import BatchAIDrafter, STATE_FILE_PATH
from src.intelligence.ai_draft_importer import AIDraftImporter


@pytest.fixture
def test_db_repo(tmp_path):
    """Fixture providing an isolated in-memory or temporary SQLite database."""
    db_file = tmp_path / "test_outreach.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    seed_mandatory_suppressions(conn)
    repo = OutreachRepository(conn)
    return repo


@pytest.fixture
def sample_professors(test_db_repo):
    """Seed sample professors across multiple time blocks and sequence orders."""
    profs = [
        ("prof_test_01", "The University of Auckland", "New Zealand", "Prof. Alpha", "alpha@auckland.ac.nz", "BLOCK_01", 1, 67, "Process Systems", "Process simulation synergy", "Neural Process Twin", "Dynamic modeling"),
        ("prof_test_02", "The University of Auckland", "New Zealand", "Prof. Beta", "beta@auckland.ac.nz", "BLOCK_01", 2, 67, "GNNs & Multi-Agent", "Systems-level ML", "LLM Inference Engine", "Kernel optimization"),
        ("prof_test_03", "UNSW Sydney", "Australia", "Prof. Gamma", "gamma@unsw.edu.au", "BLOCK_02", 1, 19, "Polymer Chemistry", "Flow chemistry overlap", "C++17 Peng-Robinson", "Kinetic modeling"),
        ("prof_test_suppressed", "TUM", "Germany", "Prof. Han Fan", "han.fan@tum.de", "BLOCK_05", 1, 28, "Robotics", "Prior reply", "SuRGE", "Perception"),
    ]
    for p_id, uni, ctry, name, email, block, order, rank, res, overlap, proj, contrib in profs:
        test_db_repo.conn.execute("""
            INSERT INTO professors (
                professor_id, university_name, country, full_name, email,
                time_block, time_block_order, qs_rank, research_areas,
                research_overlap, cv_skill_project, potential_contribution,
                outreach_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', datetime('now'), datetime('now'));
        """, (p_id, uni, ctry, name, email, block, order, rank, res, overlap, proj, contrib))
    test_db_repo.conn.commit()
    return profs


def test_cursor_persistence_and_fallback(test_db_repo, tmp_path, monkeypatch):
    """Verify cursor persists to SQLite and JSON, and falls back correctly."""
    # Isolate JSON state path to tmp_path
    temp_state = tmp_path / "drafting_state.json"
    monkeypatch.setattr("src.intelligence.batch_drafter.STATE_FILE_PATH", temp_state)

    drafter = BatchAIDrafter()
    drafter.init_cursor_table(test_db_repo)

    # Initial cursor check
    cur = drafter.get_cursor(test_db_repo)
    assert cur["last_sequence_order"] == 0
    assert cur["total_drafted"] == 0

    # Update cursor
    cur["last_sequence_order"] = 42
    cur["last_professor_id"] = "prof_test_01"
    cur["total_drafted"] = 5
    drafter.save_cursor(test_db_repo, cur)

    # Verify SQLite persistence
    loaded = drafter.get_cursor(test_db_repo)
    assert loaded["last_sequence_order"] == 42
    assert loaded["last_professor_id"] == "prof_test_01"
    assert loaded["total_drafted"] == 5

    # Verify JSON persistence
    assert temp_state.exists()
    with open(temp_state, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    assert json_data["last_sequence_order"] == 42


def test_get_next_candidates_workbook_order_and_suppression(test_db_repo, sample_professors, monkeypatch, tmp_path):
    """Verify candidate querying strictly follows block and sequence order, excluding suppressions."""
    temp_state = tmp_path / "drafting_state.json"
    monkeypatch.setattr("src.intelligence.batch_drafter.STATE_FILE_PATH", temp_state)

    drafter = BatchAIDrafter()
    candidates = drafter.get_next_candidates(test_db_repo, batch_size=10, from_beginning=True)

    # Should have 3 candidates (han.fan@tum.de is excluded by seed_mandatory_suppressions)
    assert len(candidates) == 3
    emails = [c["email"] for c in candidates]
    assert "han.fan@tum.de" not in emails
    assert candidates[0]["email"] == "alpha@auckland.ac.nz"
    assert candidates[1]["email"] == "beta@auckland.ac.nz"
    assert candidates[2]["email"] == "gamma@unsw.edu.au"


def test_stage_ai_draft_lifecycle(test_db_repo, sample_professors, monkeypatch, tmp_path):
    """Verify staging a valid AI-authored draft validates, saves, links queue, and updates cursor."""
    temp_state = tmp_path / "drafting_state.json"
    monkeypatch.setattr("src.intelligence.batch_drafter.STATE_FILE_PATH", temp_state)

    drafter = BatchAIDrafter()

    draft_payload = {
        "professor_id": "prof_test_01",
        "recipient_email": "alpha@auckland.ac.nz",
        "professor_name": "Prof. Alpha",
        "university_name": "The University of Auckland",
        "time_block_id": "BLOCK_01",
        "subject": "Summer 2027 Research Internship — Process Systems Engineering | IIT Delhi",
        "body_text": (
            "Dear Professor Alpha,\n\n"
            "My name is Harshith N, an undergraduate in Chemical Engineering at the Indian Institute of Technology Delhi (IIT Delhi). "
            "I am writing to express my strong interest in joining your group for a Summer 2027 visiting research internship.\n\n"
            "I have closely followed your research in process systems engineering and dynamic simulation. "
            "Your group's work advancing computational modeling for industrial chemical systems aligns closely with my technical priorities.\n\n"
            "In my project, Neural Process Twin, I developed physics-aware surrogate simulators in PyTorch for heat transport, "
            "achieving inference speedups while enforcing physical constraints. Combining this with my C++17 thermodynamic modeling tools, "
            "I am eager to assist in building high-fidelity simulation tooling for your group's initiatives.\n\n"
            "I would welcome the opportunity to contribute to your research for 10–12 weeks in Summer 2027. "
            "My CV and research interests statement are attached, and I would be grateful for the chance to discuss potential alignment over a brief call.\n\n"
            "Sincerely,\n"
            "Harshith N\n"
            "Undergraduate, Department of Chemical Engineering\n"
            "Indian Institute of Technology Delhi (IIT Delhi)\n"
            "ch1251635@iitd.ac.in"
        ),
        "grounding_evidence": {
            "cv_project_id": "neural-process-twin",
            "cv_project_name": "Neural Process Twin",
            "faculty_evidence_url": "https://profiles.auckland.ac.nz/alpha",
            "research_overlap": "Process systems engineering and dynamic simulation.",
            "potential_contribution": "Developing dynamic simulation modules and physics-aware surrogates."
        }
    }

    res = drafter.stage_ai_draft(test_db_repo, draft_payload)
    assert res["success"] is True
    assert "draft_id" in res["result"]

    # Verify professor status updated to DRAFT_READY
    prof = test_db_repo.get_professor("prof_test_01")
    assert prof["outreach_status"] == "DRAFT_READY"

    # Verify cursor updated
    cursor = drafter.get_cursor(test_db_repo)
    assert cursor["total_drafted"] == 1
    assert cursor["last_professor_id"] == "prof_test_01"


def test_mark_needs_research_and_failed_validation(test_db_repo, sample_professors, monkeypatch, tmp_path):
    """Verify error states update professor records and cursor counters."""
    temp_state = tmp_path / "drafting_state.json"
    monkeypatch.setattr("src.intelligence.batch_drafter.STATE_FILE_PATH", temp_state)

    drafter = BatchAIDrafter()

    # Test NEEDS_RESEARCH
    drafter.mark_needs_research(test_db_repo, "prof_test_02", "Workbook evidence URL dead")
    prof = test_db_repo.get_professor("prof_test_02")
    assert prof["outreach_status"] == "NEEDS_RESEARCH"
    assert "Workbook evidence URL dead" in prof["notes"]

    # Test FAILED_VALIDATION with invalid draft
    invalid_draft = {
        "professor_id": "prof_test_03",
        "recipient_email": "gamma@unsw.edu.au",
        "professor_name": "Prof. Gamma",
        "subject": "Invalid Subject Format",  # Violates mandatory prefix
        "body_text": "Too short body."
    }
    res = drafter.stage_ai_draft(test_db_repo, invalid_draft)
    assert res["success"] is False
    prof3 = test_db_repo.get_professor("prof_test_03")
    assert prof3["outreach_status"] == "FAILED_VALIDATION"


def test_progress_metrics_accounting(test_db_repo, sample_professors, monkeypatch, tmp_path):
    """Verify full progress metrics accounting matches database state."""
    temp_state = tmp_path / "drafting_state.json"
    monkeypatch.setattr("src.intelligence.batch_drafter.STATE_FILE_PATH", temp_state)

    drafter = BatchAIDrafter()
    metrics = drafter.get_progress_metrics(test_db_repo)

    assert metrics["total_workbook_prospects"] == 4
    # 3 active candidates + 1 suppressed (han.fan is excluded by suppression list, though in table it was NEW initially)
    assert metrics["ai_drafts_completed"] == 0
    assert metrics["needs_research_count"] == 0
    assert metrics["failed_validation_count"] == 0
