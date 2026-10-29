"""
Unit Tests for Outbound Sending Gateway, Dry-Run Simulation, and Rate Limiting.
"""

import tempfile
from pathlib import Path
import pytest
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.database.models import Professor, OutreachDraft
from src.mail.sending_gateway import SendingGateway
from src.security.permissions import SecurityRole, CampaignAuthorizationError
from config.settings import settings


@pytest.fixture
def test_repo():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_gw.db"
        initialize_database(db_path)
        with get_db(db_path) as conn:
            repo = OutreachRepository(conn)
            yield repo


def test_batch_dispatch_dry_run(test_repo):
    """Verify batch dispatch executes safely in dry run and updates statuses."""
    gateway = SendingGateway()

    # Seed 2 professors and drafts
    test_repo.upsert_professor(Professor(
        professor_id="prof_mit_01",
        university_name="MIT",
        country="United States",
        full_name="Abigail Bodner",
        email="bodner@mit.edu"
    ))
    test_repo.upsert_professor(Professor(
        professor_id="prof_supp_01",
        university_name="UCL",
        country="United Kingdom",
        full_name="Kai-Kit Wong",
        email="k.wong@ucl.ac.uk"
    ))

    draft1 = OutreachDraft(
        draft_id="draft_mit_01",
        professor_id="prof_mit_01",
        recipient_email="bodner@mit.edu",
        subject="Summer 2027 Research",
        body_text="Body content",
        word_count=150,
        status="APPROVED"
    )
    draft2 = OutreachDraft(
        draft_id="draft_supp_01",
        professor_id="prof_supp_01",
        recipient_email="k.wong@ucl.ac.uk",
        subject="Summer 2027 Research",
        body_text="Body content",
        word_count=150,
        status="APPROVED"
    )
    test_repo.save_outreach_draft(draft1)
    test_repo.save_outreach_draft(draft2)

    # Dispatch batch in dry run mode
    res = gateway.dispatch_initial_batch(
        repo=test_repo,
        draft_ids=["draft_mit_01", "draft_supp_01"],
        actor=SecurityRole.HUMAN_OPERATOR,
        operator_id="Harshith N",
        dry_run=True
    )

    assert res["dispatched_count"] == 2
    # Verify MIT prof was simulated sent
    mit_res = next(r for r in res["results"] if r["draft_id"] == "draft_mit_01")
    assert mit_res["status"] == "SIMULATED_SENT"

    # Verify Kai-Kit Wong was suppressed and aborted
    wong_res = next(r for r in res["results"] if r["draft_id"] == "draft_supp_01")
    assert wong_res["status"] == "SUPPRESSED_ABORTED"
