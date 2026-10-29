"""
Unit Tests for Email Thread Matching and Message-ID Correlation.
"""

import tempfile
from pathlib import Path
import pytest
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.database.models import Professor, EmailMessage
from src.mail.thread_matcher import ThreadMatcher


@pytest.fixture
def test_repo():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_tm.db"
        initialize_database(db_path)
        with get_db(db_path) as conn:
            repo = OutreachRepository(conn)
            yield repo


def test_subject_normalization():
    """Verify prefix removal and subject cleaning."""
    assert ThreadMatcher.normalize_subject("Re: Summer 2027") == "Summer 2027"
    assert ThreadMatcher.normalize_subject("Fwd: Re: [EXTERNAL]: Inquiry") == "Inquiry"
    assert ThreadMatcher.normalize_subject("  re:  fwd:   Subject  ") == "Subject"


def test_thread_correlation_via_in_reply_to(test_repo):
    """Verify correlation when incoming message contains In-Reply-To matching a sent message."""
    # 1. Seed professor
    test_repo.upsert_professor(Professor(
        professor_id="prof_mit_01",
        university_name="MIT",
        country="United States",
        full_name="Abigail Bodner",
        email="bodner@mit.edu"
    ))

    # 2. Seed thread and outbound message
    test_repo.get_or_create_thread("th_bodner_01", "prof_mit_01", "Summer 2027 Research")
    outbound_msg_id = "msg_outbound_12345@iitd.ac.in"
    test_repo.save_email_message(EmailMessage(
        message_id=outbound_msg_id,
        thread_id="th_bodner_01",
        professor_id="prof_mit_01",
        direction="OUTBOUND",
        sender="ch1251635@iitd.ac.in",
        recipient="bodner@mit.edu",
        subject="Summer 2027 Research",
        body="Initial outreach"
    ))

    # 3. Correlate incoming reply with In-Reply-To
    thread_id, prof_id = ThreadMatcher.correlate_incoming_message(
        repo=test_repo,
        sender_email="bodner@mit.edu",
        subject="Re: Summer 2027 Research",
        in_reply_to=f"<{outbound_msg_id}>"
    )

    assert thread_id == "th_bodner_01"
    assert prof_id == "prof_mit_01"


def test_thread_correlation_via_sender_email(test_repo):
    """Verify fallback correlation via professor's email address."""
    test_repo.upsert_professor(Professor(
        professor_id="prof_stanford_02",
        university_name="Stanford",
        country="United States",
        full_name="Percy Liang",
        email="pliang@cs.stanford.edu"
    ))

    thread_id, prof_id = ThreadMatcher.correlate_incoming_message(
        repo=test_repo,
        sender_email="pliang@cs.stanford.edu",
        subject="Regarding your inquiry",
        in_reply_to=None
    )

    assert prof_id == "prof_stanford_02"
    assert thread_id is not None
    assert thread_id.startswith("th_")
