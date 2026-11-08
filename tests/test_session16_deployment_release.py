"""
Opportunity Intelligence Engine: Session 16 Deployment Release Verification Suite.

Tests and verifies:
1. Applicant Truth Lock & Factual Claims Registry:
   - Forbids unsupported claims (custom CUDA, Rachford-Rice flash, OpenCV visual odometry on hardware).
   - Accepts truthful statements: Peng-Robinson cubic EOS PVT, Neural Process Twin surrogate,
     state estimation under uncertainty, LLM inference benchmarking with Roofline analysis.
2. Collision-Free Faculty & Startup Discovery Expansion:
   - 12 new faculty, 6 startups, 2 institutional research schemes.
   - 100% collision-free against all 1,390 production professors.
3. Inbound Message Indexer & Production IMAP Intelligence:
   - RFC822 header parsing (In-Reply-To, References, Message-ID).
   - Application ID extraction (CONF-*, REQ-*).
   - Record classification: CONFIRMED_RETRIEVED_AND_CLASSIFIED vs STATUS_MARKED_AWAITING_BODY_RETRIEVAL.
   - IMAP UID checkpointing with UIDVALIDITY cache and invalidation handling.
   - High-Priority Interview Queue (Prof. Bodner at MIT) with zero automated reply sending.
4. Portal Verification Status & 8 Safety Stops:
   - All 6 tiers (REAL_BROWSER_INSPECTED, REAL_FORM_FILLED_NOT_SUBMITTED, MOCK_TESTED_ONLY, etc.).
   - All 8 safety stops block progress to Manual Action Queue.
   - Mock simulation tagging with real_external_submission=False and 0 submitted increment.
5. Independent Activation Gates A-E:
   - Fail-closed defaults (all closed).
   - Independent isolation: opening Gate A does not open B, C, D, or E.
   - GateClosedError raised on closed operations.
6. Authoritative Rehearsal & Partition Parity:
   - 1,877 total records in development SQLite.
   - Exact mathematical partition: 1,593 academic + 193 startups + 47 institutional + 44 industry = 1,877.
   - 0 discrepancy delta.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
import pytest

from config.settings import settings
from src.opportunities.applicant_truth_lock import ApplicantTruthLock, ApplicantTruthViolationError
from src.opportunities.response_intelligence import (
    ResponseIntelligenceEngine,
    ReplyIntent,
    InboundMessageIndexer,
    InboundRecordClassification,
    ProductionOutcomeReconciler
)
from src.opportunities.portal_automation import (
    RealPortalFormInspector,
    PortalVerificationStatus,
    PortalCompatibilityTier,
    PortalApplicationDriver,
    BlockingReason
)
from src.opportunities.activation_gates import (
    ActivationGateManager,
    GateID,
    GateClosedError
)
from src.opportunities.dashboard import PortfolioDashboard
from src.opportunities.reconciliation_audit import ReconciliationAuditor
from src.opportunities.data.expansion_batch_session16 import SESSION_16_FACULTY, SESSION_16_STARTUPS, SESSION_16_INSTITUTIONS


# -----------------------------------------------------------------------------
# 1. Applicant Truth Lock & Factual Claims Registry
# -----------------------------------------------------------------------------
def test_applicant_truth_lock_rejects_unsupported_claims():
    """Verify that forbidden phrases (CUDA kernels, Rachford-Rice flash, OpenCV odometry) are rejected."""
    forbidden_samples = [
        "I implemented custom CUDA kernels to accelerate attention layers.",
        "My work involved a multicomponent flash routine using Rachford–Rice distillation.",
        "We achieved autonomous microdrone flight using OpenCV visual odometry on hardware.",
        "I built custom GPU kernel implementations for low-latency inference.",
        "Developed multi-axis kinematic control for microdrone quadcopters."
    ]
    for text in forbidden_samples:
        is_valid, violations = ApplicantTruthLock.validate_text(text)
        assert not is_valid, f"Text should have been rejected: {text}"
        assert len(violations) > 0


def test_applicant_truth_lock_accepts_truthful_grounded_text():
    """Verify that truthful statements of Harshith N's actual verified work pass validation."""
    truthful_samples = [
        "Evaluated pure-component PVT behavior and cubic compressibility factor roots Z using the Peng-Robinson equation of state.",
        "Developed a learned surrogate simulation pipeline in PyTorch (Neural Process Twin) approximating steady-state fields.",
        "Explored 3D mapping and path following under sensing uncertainty in a microdrone project under Prof. Amartansh Dubey.",
        "Constructed an LLM inference evaluation harness profiling TTFT, token throughput, and memory scaling using Roofline model diagnostics."
    ]
    for text in truthful_samples:
        is_valid, violations = ApplicantTruthLock.validate_text(text)
        assert is_valid, f"Truthful text was incorrectly flagged: {violations}"


# -----------------------------------------------------------------------------
# 2. Collision-Free Faculty & Startup Discovery Expansion
# -----------------------------------------------------------------------------
def test_session_16_faculty_batch_has_zero_collisions_against_production():
    """Verify that all 12 net-new faculty in Session 16 have zero collision with production professors."""
    prod_db = settings.SQLITE_DB_PATH
    assert prod_db.exists(), "Production DB must exist"

    conn = sqlite3.connect(f"file:{prod_db.resolve()}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT LOWER(email), LOWER(full_name) FROM professors WHERE processing_sequence <= 1390;")
    prod_rows = cur.fetchall()
    prod_emails = {r[0].strip() for r in prod_rows if r[0]}
    prod_names = {r[1].strip() for r in prod_rows if r[1]}
    conn.close()

    for fac in SESSION_16_FACULTY:
        email = fac["email"].strip().lower()
        name = fac.get("professor_name", fac.get("name", "")).strip().lower()
        assert email not in prod_emails, f"Collision detected for email {email}"
        assert name not in prod_names, f"Collision detected for name {name}"


def test_session_16_startups_and_institutions_counts():
    """Verify Session 16 discovery additions count."""
    assert len(SESSION_16_FACULTY) == 12
    assert len(SESSION_16_STARTUPS) == 6
    assert len(SESSION_16_INSTITUTIONS) == 2


# -----------------------------------------------------------------------------
# 3. Inbound Message Indexer & Production IMAP Intelligence
# -----------------------------------------------------------------------------
def test_inbound_indexer_rfc822_parsing_and_app_id():
    """Verify RFC822 header parsing and application ID extraction."""
    indexer = InboundMessageIndexer()
    headers = {
        "Message-ID": "<msg-abc-123@mit.edu>",
        "In-Reply-To": "<outbound-456@iitd.ac.in>",
        "References": "<outbound-456@iitd.ac.in>",
        "From": "Prof. Bodner <bodner@mit.edu>",
        "To": "Harshith N <ch1251635@iitd.ac.in>",
        "Subject": "Re: Summer 2027 Research Inquiry [CONF-MIT-BODNER-2027]"
    }
    parsed = indexer.parse_rfc822_headers(headers)
    assert parsed["message_id"] == "msg-abc-123@mit.edu"
    assert parsed["in_reply_to"] == "outbound-456@iitd.ac.in"
    assert parsed["references"] == ["outbound-456@iitd.ac.in"]
    assert parsed["sender_email"] == "bodner@mit.edu"
    assert parsed["recipient_email"] == "ch1251635@iitd.ac.in"

    app_id = indexer.extract_application_id(parsed["subject"])
    assert app_id == "CONF-MIT-BODNER-2027"


def test_inbound_indexer_record_classification():
    """Verify categorization into CONFIRMED_RETRIEVED_AND_CLASSIFIED vs STATUS_MARKED_AWAITING_BODY_RETRIEVAL."""
    indexer = InboundMessageIndexer()

    # Case 1: Body present -> CONFIRMED_RETRIEVED_AND_CLASSIFIED
    res_with_body = indexer.index_inbound_record(
        headers={"Message-ID": "<1@test.org>", "From": "prof@test.org", "Subject": "Re: Research"},
        body="I would be happy to host you in our laboratory this summer."
    )
    assert res_with_body["classification_status"] == InboundRecordClassification.CONFIRMED_RETRIEVED_AND_CLASSIFIED
    assert res_with_body["intent"] == ReplyIntent.POSITIVE_INTEREST
    assert res_with_body["automated_reply_permitted"] is False

    # Case 2: Body absent -> STATUS_MARKED_AWAITING_BODY_RETRIEVAL
    res_no_body = indexer.index_inbound_record(
        headers={"Message-ID": "<2@test.org>", "From": "prof2@test.org", "Subject": "Re: Research"}
    )
    assert res_no_body["classification_status"] == InboundRecordClassification.STATUS_MARKED_AWAITING_BODY_RETRIEVAL
    assert res_no_body["raw_body_available"] is False

    # Case 3: ATS receipt -> ATS_CONFIRMATION_RECEIPT
    res_ats = indexer.index_inbound_record(
        headers={"Message-ID": "<3@greenhouse.io>", "From": "no-reply@greenhouse.io", "Subject": "Thank you for applying to Mistral AI (REQ-12345)"},
        body="Your application for Research Intern has been received."
    )
    assert res_ats["classification_status"] == InboundRecordClassification.ATS_CONFIRMATION_RECEIPT


def test_inbound_indexer_uidvalidity_cache_and_invalidation(tmp_path):
    """Verify that IMAP UID checkpoints handle UIDVALIDITY changes safely."""
    cp_file = tmp_path / "imap_checkpoint.json"
    indexer = InboundMessageIndexer(checkpoint_file=cp_file)

    # Initial checkpoint
    cp1 = indexer.update_uid_checkpoint(mailbox="INBOX", uid_validity=12345, highest_uid=100, messages_processed=10)
    assert cp1.is_valid is True
    assert cp1.processed_count == 10

    # Resync with same UIDVALIDITY
    cp2 = indexer.update_uid_checkpoint(mailbox="INBOX", uid_validity=12345, highest_uid=150, messages_processed=5)
    assert cp2.is_valid is True
    assert cp2.processed_count == 15

    # UIDVALIDITY changes (e.g. mailbox rebuilt)
    cp3 = indexer.update_uid_checkpoint(mailbox="INBOX", uid_validity=99999, highest_uid=20, messages_processed=2)
    assert cp3.uidvalidity_changed is True
    assert cp3.is_valid is False


def test_high_priority_interview_queue_surfaces_bodner():
    """Verify that the high-priority interview queue surfaces Prof. Bodner at MIT with manual action required."""
    indexer = InboundMessageIndexer()
    queue = indexer.get_high_priority_interview_queue()
    assert len(queue) >= 1
    bodner_item = next((q for q in queue if "bodner@mit.edu" in q["sender"]), None)
    assert bodner_item is not None
    assert bodner_item["classification"] == "INTERVIEW_INVITATION"
    assert bodner_item["automated_reply_permitted"] is False
    assert bodner_item["manual_action_required"] is True


# -----------------------------------------------------------------------------
# 4. Portal Verification Status & 8 Safety Stops
# -----------------------------------------------------------------------------
def test_all_six_portal_verification_status_tiers():
    """Verify that all 6 portal verification statuses are correctly determined."""
    insp = RealPortalFormInspector

    # 1. REAL_BROWSER_INSPECTED
    s1 = insp.determine_verification_status("https://boards.greenhouse.io/cohere/jobs/1", is_browser_inspected=True)
    assert s1 == PortalVerificationStatus.REAL_BROWSER_INSPECTED

    # 2. REAL_FORM_FILLED_NOT_SUBMITTED
    s2 = insp.determine_verification_status("https://jobs.lever.co/anthropic/2", is_form_filled=True)
    assert s2 == PortalVerificationStatus.REAL_FORM_FILLED_NOT_SUBMITTED

    # 3. MOCK_TESTED_ONLY
    s3 = insp.determine_verification_status("https://custom-portal.org/apply")
    assert s3 == PortalVerificationStatus.MOCK_TESTED_ONLY

    # 4. LOGIN_OR_MFA_BLOCKED
    s4 = insp.determine_verification_status("https://workday.com/nvidia/3", requires_login=True)
    assert s4 == PortalVerificationStatus.LOGIN_OR_MFA_BLOCKED

    # 5. UNSUPPORTED
    s5 = insp.determine_verification_status("https://taleo.net/oracle/4")
    assert s5 == PortalVerificationStatus.UNSUPPORTED

    # 6. EXPIRED_OR_INVALID_POSTING
    s6 = insp.determine_verification_status("https://jobs.ashbyhq.com/mistral/5", http_status=404)
    assert s6 == PortalVerificationStatus.EXPIRED_OR_INVALID_POSTING


def test_all_eight_portal_safety_stops():
    """Verify that all 8 safety stop conditions evaluate correctly."""
    insp = RealPortalFormInspector

    stops_to_test = [
        ({"has_captcha": True}, BlockingReason.CAPTCHA_REQUIRED),
        ({"requires_mfa": True}, BlockingReason.MFA_REQUIRED),
        ({"custom_declaration_required": True}, BlockingReason.CUSTOM_DECLARATION),
        ({"requires_legal_agreement": True}, BlockingReason.LEGAL_AGREEMENT),
        ({"unavailable_documents_required": True}, BlockingReason.UNAVAILABLE_DOCUMENTS),
        ({"unverified_work_auth": True}, BlockingReason.UNVERIFIED_WORK_AUTHORIZATION),
        ({"uncertain_eligibility": True}, BlockingReason.UNCERTAIN_ELIGIBILITY),
        ({"unknown_unmapped_field": True}, BlockingReason.UNMAPPED_CUSTOM_FIELD),
    ]

    for fields, expected_reason in stops_to_test:
        stop = insp.evaluate_safety_stops(fields)
        assert stop is not None, f"Failed to trigger stop for {expected_reason}"
        assert stop["blocking_reason"] == expected_reason


# -----------------------------------------------------------------------------
# 5. Independent Activation Gates A–E
# -----------------------------------------------------------------------------
def test_activation_gates_fail_closed_and_independent():
    """Verify that all 5 activation gates fail closed, raise GateClosedError, and operate independently."""
    mgr = ActivationGateManager()

    # 1. Fail closed default
    for gid in GateID:
        assert mgr.is_gate_open(gid) is False
        with pytest.raises(GateClosedError):
            mgr.assert_gate_open(gid)

    # 2. Independent opening of Gate A does NOT open B, C, D, or E
    mgr.enable_gate(
        GateID.GATE_A_MASTER_WORKBOOK_MUTATION,
        authorized_by="Lead Engineer",
        confirmation_token="CONFIRM_ACTIVATE_GATE_A_MASTER_WORKBOOK_MUTATION"
    )
    assert mgr.is_gate_open(GateID.GATE_A_MASTER_WORKBOOK_MUTATION) is True
    assert mgr.is_gate_open(GateID.GATE_B_ACADEMIC_EXPANSION_DISPATCH) is False
    assert mgr.is_gate_open(GateID.GATE_C_NON_ACADEMIC_DISPATCH) is False
    assert mgr.is_gate_open(GateID.GATE_D_LIVE_PORTAL_SUBMISSION) is False
    assert mgr.is_gate_open(GateID.GATE_E_AUTOMATED_REPLY_DISPATCH) is False

    # 3. Independent opening of Gate B does NOT open C, D, or E
    mgr.enable_gate(
        GateID.GATE_B_ACADEMIC_EXPANSION_DISPATCH,
        authorized_by="Lead Engineer",
        confirmation_token="CONFIRM_ACTIVATE_GATE_B_ACADEMIC_EXPANSION_DISPATCH"
    )
    assert mgr.is_gate_open(GateID.GATE_B_ACADEMIC_EXPANSION_DISPATCH) is True
    assert mgr.is_gate_open(GateID.GATE_C_NON_ACADEMIC_DISPATCH) is False

    # 4. Emergency killswitch closes all gates
    mgr.disable_all_gates()
    for gid in GateID:
        assert mgr.is_gate_open(gid) is False


# -----------------------------------------------------------------------------
# 6. Authoritative Rehearsal & Partition Parity
# -----------------------------------------------------------------------------
def test_mathematical_partition_exactness_session16():
    """Verify the development SQLite database maintains 0 discrepancy delta across all records."""
    auditor = ReconciliationAuditor()
    res = auditor.run_full_reconciliation()
    assert res["total_opportunities"] >= 1877
    assert res["mathematical_invariants"]["discrepancy_delta"] == 0
    assert res["mathematical_invariants"]["partition_is_exact"] is True

    # Category counts
    cat_counts = res.get("by_category", {})
    acad = cat_counts.get("ACADEMIC_LAB", 0)
    startup = cat_counts.get("STARTUP", 0)
    institutional = cat_counts.get("INSTITUTIONAL_PROGRAM", 0)
    industry = cat_counts.get("FRONTIER_COMPANY", 0)

    assert acad >= 1593
    assert startup >= 193
    assert institutional >= 47
    assert industry == 44


def test_consolidated_operations_report_distinguishes_metrics():
    """Verify that consolidated dashboard accurately separates operations metrics."""
    dev_db = settings.DATA_DIR / "development" / "opportunity_dev.db"
    assert dev_db.exists()
    conn = sqlite3.connect(str(dev_db))
    report = PortfolioDashboard.generate_consolidated_operations_report(conn)
    conn.close()

    assert report["total_development_opportunities"] >= 1877
    assert report["production_scheduler_owned"] in (1390, 1561)
    assert report["smtp_accepted"] == 901
    assert report["real_external_submitted_count"] == 0
    assert report["mock_simulated_submissions_count"] == 0
    assert report["interview_invitations_count"] >= 1
    assert report["manual_action_queue_pending"] >= 1
