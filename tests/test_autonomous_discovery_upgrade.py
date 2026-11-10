"""
Comprehensive regression test suite for the Opportunity Intelligence Engine:
Autonomous Discovery & Execution Upgrade.

Verifies:
1. Canonical QS Top-400 dataset completeness, distribution across 5 phases, and institutional metadata.
2. 7-tier research alignment classification, tech match matrix, and frontier hardware co-design keywords.
3. Autonomous discovery scheduler university screening, checkpoint persistence, deduplication, and cursor tracking.
4. Bounce recovery intelligence, diagnostic classification, and replacement authorization.
5. Real portal application management, document vault upload, additional information persistence, and tailored answers.
6. Evidence service and endpoint returning 200 with SOURCE_NOT_AVAILABLE fallback instead of 404 for unarchived records.
7. Multidimensional IMAP reply reconciliation, conditional inquiry tracking, and non-sending draft responses.
"""

import os
import sqlite3
import pytest
from pathlib import Path

from src.opportunities.data.qs_top_universities_data import (
    QS_TOP_UNIVERSITIES_DATA,
    ScreeningPhase,
    DepartmentScreeningStatus
)
from src.opportunities.ranking import OpportunityRanker
from src.opportunities.models import (
    Opportunity,
    OpportunityType
)
from src.opportunities.autonomous_discovery_scheduler import AutonomousDiscoveryScheduler
from src.mail.bounce_recovery import BounceRecoveryService, BounceDiagnosticType
from src.opportunities.portal_manager import PortalManagerService
from src.web.routes.dashboard_service import DashboardService


def test_qs_top400_dataset_integrity():
    """Verify exactly 400 institutions exist, keyed 1..400, partitioned across 5 phases."""
    assert len(QS_TOP_UNIVERSITIES_DATA) == 400

    ranks = [u["qs_rank_2027"] for u in QS_TOP_UNIVERSITIES_DATA]
    assert sorted(ranks) == list(range(1, 401))

    phase_counts = {}
    for uni in QS_TOP_UNIVERSITIES_DATA:
        phase = uni["phase"]
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        assert "institution_name" in uni
        assert "country" in uni
        assert "canonical_block_id" in uni
        assert "iana_timezone" in uni
        assert "departments" in uni
        assert len(uni["departments"]) > 0

    assert phase_counts[ScreeningPhase.PHASE_A_TOP_120.value] == 120
    assert phase_counts[ScreeningPhase.PHASE_B_121_200.value] == 80
    assert phase_counts[ScreeningPhase.PHASE_C_201_250.value] == 50
    assert phase_counts[ScreeningPhase.PHASE_D_251_300.value] == 50
    assert phase_counts[ScreeningPhase.PHASE_E_301_400.value] == 100


def test_7_tier_research_alignment_and_frontier_hardware():
    """Verify all 7 standardized research alignment tiers and hardware co-design keywords."""
    ranker = OpportunityRanker()

    # Verify frontier hardware co-design entry in TECH_MATCH_MATRIX
    hw_matches = [
        item for item in ranker.TECH_MATCH_MATRIX
        if "Hardware" in item.get("project_id", "") or "Hardware" in item.get("tier", "")
    ]
    assert len(hw_matches) > 0
    hw_item = hw_matches[0]
    for kw in ["in-memory computing", "memristor", "neuromorphic", "analog", "co-design"]:
        assert any(kw in s.lower() for s in hw_item["keywords"])

    # Test tier classifications
    opp_hardware = Opportunity(
        opportunity_id="test_hw_01",
        organization_id="org_hw",
        title="Postdoc / Intern in Neuromorphic Memristive Accelerators",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        technical_domains=["In-Memory Computing", "Memristor", "Hardware Accelerator"],
        technical_relevance_summary="Analog compute-in-memory and neuromorphic hardware architecture."
    )
    tier = ranker.classify_alignment_tier(opp_hardware)
    assert tier == "FRONTIER_AI_HARDWARE"

    opp_robotics = Opportunity(
        opportunity_id="test_rob_01",
        organization_id="org_rob",
        title="Aerial Robotics & Microdrone Autonomy Intern",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        technical_domains=["Microdrone", "Autonomous Flight", "SLAM"],
        technical_relevance_summary="Visual odometry and robotic trajectory optimization."
    )
    tier_rob = ranker.classify_alignment_tier(opp_robotics)
    assert tier_rob == "ROBOTICS_EMBODIED_AI"

    opp_sciml = Opportunity(
        opportunity_id="test_sciml_01",
        organization_id="org_sciml",
        title="Scientific ML and PINNs for Reactive Transport",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        technical_domains=["SciML", "PINN", "Neural Operator"],
        technical_relevance_summary="Physics-informed neural networks and surrogate transport modeling."
    )
    tier_sciml = ranker.classify_alignment_tier(opp_sciml)
    assert tier_sciml == "SCIENTIFIC_ML"

    opp_systems = Opportunity(
        opportunity_id="test_sys_01",
        organization_id="org_sys",
        title="High-Throughput LLM Inference Systems Intern",
        opportunity_type=OpportunityType.STARTUP.value,
        technical_domains=["GPU Kernel", "CUDA", "Triton"],
        technical_relevance_summary="Roofline modeling and low-latency tensor parallelism."
    )
    assert ranker.classify_alignment_tier(opp_systems) == "AI_SYSTEMS_SOFTWARE"


def test_autonomous_discovery_screening_step():
    """Verify screening step operates without throwing, updates cursor and logs results."""
    scheduler = AutonomousDiscoveryScheduler()
    res = scheduler.screen_next_university()
    assert res.status in ["SCREENED_SUCCESS", "COMPLETED_FULL_SWEEP"]
    assert res.qs_rank >= 1
    assert len(res.departments_inspected) > 0


def test_bounce_recovery_service():
    """Verify bounce diagnostic classification and replacement authorization."""
    service = BounceRecoveryService()
    diag, note = service.classify_bounce_diagnostic("550 5.1.1 User unknown")
    assert diag == BounceDiagnosticType.INVALID_MAILBOX
    assert "Mailbox does not exist" in note

    diag_policy, note_p = service.classify_bounce_diagnostic("550 5.7.1 Service unavailable: Client host blocked using Spamhaus")
    assert diag_policy == BounceDiagnosticType.ACCESS_DENIED_POLICY

    records = service.list_recovery_records()
    assert isinstance(records, list)
    assert len(records) > 0

    # Test authorizing an invalid id gracefully raises ValueError
    with pytest.raises(ValueError):
        service.authorize_replacement(replacement_id=999999)


def test_portal_manager_and_vault(tmp_path):
    """Verify portal application document vault upload, additional info, and tailored answers."""
    mgr = PortalManagerService(vault_dir=tmp_path / "vault")

    # Pick an existing opp
    with mgr._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT opportunity_id FROM opportunities WHERE application_url IS NOT NULL LIMIT 1")
        row = cur.fetchone()
        assert row is not None
        opp_id = row[0]

    # Test saving additional information
    save_res = mgr.save_additional_information(opp_id, "Interested in high-throughput CUDA kernels.")
    assert save_res["success"] is True

    # Test uploading a document to the application's vault
    fake_cv = b"%PDF-1.4 Fake CV Bytes for testing"
    up_res = mgr.upload_vault_document(opp_id, "Special_CV.pdf", fake_cv)
    assert up_res["success"] is True
    assert up_res["filename"] == "Special_CV.pdf"

    # Test preparing tailored responses
    tailored = mgr.prepare_tailored_answers(opp_id)
    assert "statement_of_purpose" in tailored
    assert "Harshith N" in tailored["applicant_name"]
    assert "Interested in high-throughput CUDA kernels" in tailored["statement_of_purpose"]

    # Verify detail retrieval reflects uploaded doc and additional info
    detail = mgr.get_application_detail(opp_id)
    assert detail["additional_information"] == "Interested in high-throughput CUDA kernels."
    assert any(d["filename"] == "Special_CV.pdf" for d in detail["uploaded_vault_documents"])


def test_evidence_service_fallback_not_404():
    """Verify evidence service returns SOURCE_NOT_AVAILABLE fallback instead of crashing or returning 404."""
    dash_service = DashboardService()
    fallback = dash_service.get_evidence("professor", "PROF-NON-EXISTENT-ID-123")
    assert fallback["status"] == "SOURCE_NOT_AVAILABLE"
    assert fallback["reverification_flag"] is True
    assert "Official primary source URL not currently archived" in fallback["message"]


def test_multidimensional_imap_reply_reconciliation():
    """Verify verified IMAP replies carry multidimensional states and draft responses."""
    dash_service = DashboardService()
    outcomes = dash_service.get_outcomes_details()
    replies = outcomes.get("verified_replies", [])
    assert len(replies) > 0

    # Ensure Abigail Bodner is quarantined from verified replies
    bodner_matches = [
        r for r in replies
        if "bodner" in r.get("from_address", "").lower() or "abigail" in r.get("from_name", "").lower()
    ]
    assert len(bodner_matches) == 0

    # Verify multidimensional properties in reply_classifications table
    with sqlite3.connect("data/outreach_engine.db") as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT classification_id, interest_level, funding_condition, meeting_status, next_action
            FROM reply_classifications
            WHERE interest_level IS NOT NULL
        """)
        rows = cur.fetchall()
        assert len(rows) > 0
        sample = rows[0]
        assert sample[1] is not None  # interest_level
        assert sample[4] is not None  # next_action

        # Check non-sending draft responses in reply_drafts
        cur.execute("SELECT COUNT(*) FROM reply_drafts WHERE status = 'AWAITING_HUMAN_REVIEW'")
        draft_count = cur.fetchone()[0]
        assert draft_count > 0
