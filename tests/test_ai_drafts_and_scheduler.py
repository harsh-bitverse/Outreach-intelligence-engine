"""
Unit and Integration Tests for AI-Authored Draft Importer, Validation Gates,
and Independent Scheduler Consumption of Staged Drafts.
"""

import tempfile
from pathlib import Path
import pytest

from config.settings import settings
from src.database.connection import get_db, initialize_database
from src.database.models import Professor
from src.database.repository import OutreachRepository
from src.intelligence.ai_draft_importer import AIDraftImporter, ai_draft_importer
from src.scheduler.queue_runner import AutonomousQueueRunner


@pytest.fixture
def isolated_ai_env():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_ai_drafts.db"
        initialize_database(db_path)
        with get_db(db_path) as conn:
            repo = OutreachRepository(conn)
            runner = AutonomousQueueRunner()
            runner.seed_reply_received_suppressions(repo)
            yield repo, runner, db_path


def test_ai_draft_validation_gates(isolated_ai_env):
    """Test structural, word-count, subject, and suppression validation gates."""
    repo, _, _ = isolated_ai_env
    importer = AIDraftImporter()

    # Seed professor
    repo.upsert_professor(Professor(
        professor_id="prof_ai_01",
        university_name="MIT",
        country="United States",
        full_name="Connor Coley",
        email="ccoley@mit.edu",
        time_block="BLOCK_10",
        time_block_order=906
    ))

    # 1. Invalid Subject Format (missing suffix)
    bad_subject = {
        "professor_id": "prof_ai_01",
        "recipient_email": "ccoley@mit.edu",
        "subject": "Summer 2027 Research Internship — AI Discovery",  # Missing | IIT Delhi
        "body_text": "Dear Professor Coley,\n\nP1\n\nP2\n\nP3\n\nP4\n\nSincerely,\nHarshith N\nUndergraduate, Department of Chemical Engineering\nIndian Institute of Technology Delhi (IIT Delhi)\nch1251635@iitd.ac.in",
        "grounding_evidence": {"cv_project_id": "thermo-cpp-solver", "faculty_evidence_url": "https://mit.edu"}
    }
    is_valid, err = importer.validate_draft(bad_subject, repo)
    assert not is_valid
    assert "Subject Gate Failed" in err

    # 2. Prohibited Link in Signature / Body
    bad_link = {
        "professor_id": "prof_ai_01",
        "recipient_email": "ccoley@mit.edu",
        "subject": "Summer 2027 Research Internship — AI Discovery | IIT Delhi",
        "body_text": "Dear Professor Coley,\n\nP1\n\nP2\n\nP3\n\nP4\n\nSincerely,\nHarshith N\nUndergraduate, Department of Chemical Engineering\nIndian Institute of Technology Delhi (IIT Delhi)\nch1251635@iitd.ac.in\nlinkedin.com/in/harshith",
        "grounding_evidence": {"cv_project_id": "thermo-cpp-solver", "faculty_evidence_url": "https://mit.edu"}
    }
    is_valid, err = importer.validate_draft(bad_link, repo)
    assert not is_valid
    assert "Prohibited external links" in err

    # 3. Suppressed Recipient (one of the 6 confirmed replied contacts)
    suppressed_draft = {
        "professor_id": "prof_tum_fan",
        "recipient_email": "han.fan@tum.de",
        "subject": "Summer 2027 Research Internship — Robotics | IIT Delhi",
        "body_text": "Dear Professor Fan,\n\nP1\n\nP2\n\nP3\n\nP4\n\nSincerely,\nHarshith N\nUndergraduate, Department of Chemical Engineering\nIndian Institute of Technology Delhi (IIT Delhi)\nch1251635@iitd.ac.in",
        "grounding_evidence": {"cv_project_id": "thermo-cpp-solver", "faculty_evidence_url": "https://tum.de"}
    }
    is_valid, err = importer.validate_draft(suppressed_draft, repo)
    assert not is_valid
    assert "Safety Gate Blocked" in err


def test_ai_draft_import_and_independent_scheduler_dispatch(isolated_ai_env):
    """Test importing genuine AI drafts and dispatching them independently via the scheduler."""
    repo, runner, _ = isolated_ai_env
    importer = AIDraftImporter()

    # Seed 2 professors (Brent Young in BLOCK_01, Connor Coley in BLOCK_10)
    repo.upsert_professor(Professor(
        professor_id="prof_0209dcbdc769304f",
        university_name="The University of Auckland",
        country="New Zealand",
        full_name="Brent Young",
        email="b.young@auckland.ac.nz",
        time_block="BLOCK_01",
        time_block_order=1
    ))
    repo.upsert_professor(Professor(
        professor_id="prof_b59389c8894bc7aa",
        university_name="Massachusetts Institute of Technology (MIT)",
        country="United States",
        full_name="Connor W. Coley",
        email="ccoley@mit.edu",
        time_block="BLOCK_10",
        time_block_order=906
    ))

    # Valid AI-Authored Draft for Brent Young (174 content words)
    young_body = (
        "Dear Professor Young,\n\n"
        "My name is Harshith N, an undergraduate in Chemical Engineering at the Indian Institute of Technology Delhi (IIT Delhi). "
        "I am writing to express my strong interest in joining your group at the Industrial Information and Control Centre for a Summer 2027 visiting research internship.\n\n"
        "I have closely followed your research in process systems engineering, dynamic process simulation, and digital twins for industrial operations. "
        "Your group's work advancing computational modeling and real-time optimization for complex chemical and energy processing systems aligns closely with my technical priorities.\n\n"
        "In my project, Neural Process Twin, I developed physics-aware surrogate simulators in PyTorch for heat conduction, achieving real-time inference speedups while enforcing physical governing constraints. "
        "Combining this with my C++17 thermodynamic modeling tools, I am eager to assist in building high-fidelity dynamic models and simulation tooling for your ongoing industrial initiatives.\n\n"
        "I would welcome the opportunity to contribute to your process simulation research for 10–12 weeks in Summer 2027. "
        "My CV is attached, and I would be grateful for the chance to discuss potential alignment over a brief discussion.\n\n"
        "Sincerely,\n"
        "Harshith N\n"
        "Undergraduate, Department of Chemical Engineering\n"
        "Indian Institute of Technology Delhi (IIT Delhi)\n"
        "ch1251635@iitd.ac.in"
    )

    draft_payload = [{
        "professor_id": "prof_0209dcbdc769304f",
        "recipient_email": "b.young@auckland.ac.nz",
        "professor_name": "Brent Young",
        "university_name": "The University of Auckland",
        "time_block_id": "BLOCK_01",
        "subject": "Summer 2027 Research Internship — Process Systems Engineering & Digital Twins | IIT Delhi",
        "body_text": young_body,
        "grounding_evidence": {
            "cv_project_id": "neural-process-twin",
            "faculty_evidence_url": "https://profiles.auckland.ac.nz/b-young"
        }
    }]

    # 1. Import AI draft into SQLite
    import_res = importer.import_drafts(draft_payload, repo)
    assert import_res["imported_count"] == 1
    assert import_res["rejected_count"] == 0

    # Verify queue record
    queued = repo.list_queue_items(time_block="BLOCK_01")
    assert len(queued) == 1
    item = queued[0]
    assert item["recipient_email"] == "b.young@auckland.ac.nz"
    assert item["status"] == "PENDING"
    assert item["draft_subject"] == "Summer 2027 Research Internship — Process Systems Engineering & Digital Twins | IIT Delhi"

    # 2. Standalone Scheduler consumes and dispatches the draft (dry run)
    dispatch_res = runner.dispatch_queue(
        repo=repo,
        time_block="BLOCK_01",
        max_messages=1,
        dry_run=True,
        force_window=True
    )

    assert dispatch_res["dispatched_count"] == 1
    dispatched_item = dispatch_res["items"][0]
    assert dispatched_item["status"] == "SIMULATED_SENT"
    assert dispatched_item["message_id"].startswith("<") and "@iitd.ac.in>" in dispatched_item["message_id"]

    # 3. Duplicate Prevention Verification
    # Re-running dispatch must find 0 pending items
    dispatch_res_2 = runner.dispatch_queue(
        repo=repo,
        time_block="BLOCK_01",
        max_messages=1,
        dry_run=True,
        force_window=True
    )
    assert dispatch_res_2["dispatched_count"] == 0

    # Verify queue status transitioned to SIMULATED_SENT in SQLite
    cur = repo.conn.cursor()
    cur.execute("SELECT status, dispatched_at FROM outreach_queue WHERE professor_id = 'prof_0209dcbdc769304f';")
    row = cur.fetchone()
    assert row[0] == "SIMULATED_SENT"
    assert row[1] is not None
