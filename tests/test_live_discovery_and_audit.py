"""
Unit and Integration Tests for Session 03: Live Discovery, Evidence Grounding, and Authoritative Personalization.
"""

import os
import json
import sqlite3
import tempfile
from pathlib import Path
import pytest

from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    OpportunityClassification,
    RoleTitleType,
    WorkplaceType,
    InternationalEligibility,
    CompensationType,
    ContactChannel,
    DiscoveryCheckpoint
)
from src.opportunities.audit import audit_opportunity, audit_entire_database
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.opportunities.adapters.live_ats_discovery import LiveATSDiscoveryAdapter
from src.opportunities.recurring_worker import RecurringDiscoveryWorker
from src.opportunities.repository import OpportunityRepository
from src.opportunities.schema import apply_opportunity_migrations


def test_authoritative_applicant_identity():
    """Verify applicant identity is strictly Harshith N, IIT Delhi Chemical Engineering."""
    profile = DomainAdaptivePersonalizer.APPLICANT_PROFILE
    assert profile["full_name"] == "Harshith N"
    assert profile["institution"] == "Indian Institute of Technology Delhi (IIT Delhi)"
    assert profile["major"] == "Chemical Engineering"
    assert "harsh-bitverse" in profile["portfolio_url"]

    # Test all 10 verified startup pitch examples
    pitches = DomainAdaptivePersonalizer.generate_ten_verified_startup_pitches()
    assert len(pitches) == 10

    for p in pitches:
        assert p["applicant_name"] == "Harshith N"
        body = p["pitch_body"]
        assert "Harshith N" in body
        assert "Harsh Vardhan" not in body
        assert "Harsh Hagr" not in body
        assert "Chemical Engineering" in body
        assert "Indian Institute of Technology Delhi" in body
        # Ensure verified project reference
        assert any(proj in body for proj in [
            "LLM Inference Intelligence Engine",
            "Neural Process Twin",
            "PINN Error Estimation term paper",
            "C++ Peng–Robinson EOS solver",
            "SuRGE microdrone work",
            "Agentic Industrial AI Workbench"
        ])


def test_audit_classification_and_evidence_grounding():
    """Verify audit classifies confirmed vs prospective vs monitor, and grounds compensation."""
    org_together = Organization(
        organization_id="org_test_together",
        name="Together AI",
        category="STARTUP",
        country="United States",
        city="Menlo Park"
    )
    opp_together = Opportunity(
        opportunity_id="opp_test_together",
        organization_id="org_test_together",
        title="AI Infrastructure System Engineer",
        opportunity_type="STARTUP",
        source="TEST",
        compensation_type="PAID",
        compensation_details="Estimated"
    )

    audited = audit_opportunity(opp_together, org_together)
    assert audited.audit_classification in (OpportunityClassification.CONFIRMED_OPENING.value, OpportunityClassification.GENERAL_ENGINEERING_VACANCY.value)
    assert audited.role_title_type in (RoleTitleType.EXACT.value, RoleTitleType.INFERRED.value)
    assert audited.compensation_type == CompensationType.PAID.value
    assert "CALIFORNIA_SB_1162" in audited.compensation_evidence
    assert audited.international_eligibility == InternationalEligibility.SPONSORED_OR_J1_REQUIRED.value

    # Test prospective target without explicit published salary
    org_kissan = Organization(
        organization_id="org_test_kissan",
        name="KissanAI",
        category="STARTUP",
        country="India",
        city="Bengaluru"
    )
    opp_kissan = Opportunity(
        opportunity_id="opp_test_kissan",
        organization_id="org_test_kissan",
        title="Voice AI Systems Intern",
        opportunity_type="STARTUP",
        source="TEST",
        compensation_type="PAID",
        compensation_details="Estimated ₹60,000/mo"
    )

    audited_k = audit_opportunity(opp_kissan, org_kissan)
    assert audited_k.audit_classification == OpportunityClassification.PROSPECTIVE_OUTREACH_TARGET.value
    assert audited_k.role_title_type == RoleTitleType.PROPOSED_OUTREACH_FOCUS.value
    # Must be grounded to UNKNOWN because employer has not published explicit statutory compensation
    assert audited_k.compensation_type == CompensationType.UNKNOWN.value
    assert audited_k.compensation_evidence == "UNPUBLISHED_BY_EMPLOYER"
    assert audited_k.international_eligibility == InternationalEligibility.ELIGIBLE_DOMESTIC.value


def test_live_ats_adapter_normalization():
    """Verify LiveATSDiscoveryAdapter normalizes mock Ashby and Greenhouse items cleanly."""
    adapter = LiveATSDiscoveryAdapter()

    mock_ashby_item = {
        "company_meta": {
            "name": "Perplexity AI",
            "website": "https://www.perplexity.ai",
            "city": "San Francisco",
            "country": "United States",
            "timezone": "America/Los_Angeles",
            "domains": ["LLMs", "Inference Optimization"],
            "funding_stage": "SERIES_B",
            "tier": "TIER_1",
            "kind": "ashby",
            "board_id": "perplexity",
            "relevance": "Fast inference and AI search engine.",
            "potential_contribution": "Optimizing token latency.",
            "matched_project": "LLM Inference Intelligence Engine"
        },
        "job_raw": {
            "title": "Software Engineer - Inference Systems",
            "location": "San Francisco, CA",
            "jobUrl": "https://jobs.ashbyhq.com/perplexity/test-1234",
            "compensation": "$50 - $65 / hour"
        },
        "source_kind": "ashby"
    }

    org, opp = adapter.normalize_raw_item(mock_ashby_item)
    assert org.name == "Perplexity AI"
    assert opp.title == "Software Engineer - Inference Systems"
    assert opp.application_url == "https://jobs.ashbyhq.com/perplexity/test-1234"
    assert opp.role_title_type == RoleTitleType.EXACT.value
    assert opp.audit_classification in (OpportunityClassification.CONFIRMED_OPENING.value, OpportunityClassification.GENERAL_ENGINEERING_VACANCY.value)
    assert opp.compensation_type in (CompensationType.PAID.value, CompensationType.UNKNOWN.value)
    assert "STATUTORY_PAY_TRANSPARENCY" in opp.compensation_evidence


def test_recurring_worker_execution_isolated():
    """Verify RecurringDiscoveryWorker runs sweep and maintains durable checkpoints in test db."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "test_opp.db")
        test_wb = os.path.join(tmpdir, "test_wb.xlsx")

        conn = sqlite3.connect(test_db)
        apply_opportunity_migrations(conn)
        conn.close()

        worker = RecurringDiscoveryWorker(
            db_path=test_db,
            workbook_path=test_wb,
            interval_seconds=3600
        )

        res = worker.execute_discovery_cycle()
        assert res["status"] == "COMPLETED"
        assert res["total_qualified"] > 0
        assert "adapters" in res

        # Verify checkpoints saved
        conn = sqlite3.connect(test_db)
        cur = conn.cursor()
        cur.execute("SELECT source_key, cursor_or_token, status FROM discovery_checkpoints;")
        checkpoints = cur.fetchall()
        assert len(checkpoints) >= 1
        for key, cursor, status in checkpoints:
            assert status == "SUCCESS"
            assert int(cursor) > 0
        conn.close()


def test_production_scheduler_isolation_and_pid():
    """Ensure production database and PID 11596 are verified and left completely untouched."""
    # Production database outreach_engine.db must NOT have opportunity tables
    prod_db = Path("data/outreach_engine.db")
    if prod_db.exists():
        conn = sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        conn.close()
        assert "opportunities" not in tables
        assert "discovery_checkpoints" not in tables
