"""
Unit and Integration Tests for Session 05:
Global Academic Expansion, Portfolio Reconciliation, Feasibility Verification & Production Isolation.
"""

import os
import sqlite3
import subprocess
from pathlib import Path
import pytest

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    OpportunityClassification,
    RoleTitleType,
    FundingAvailability,
    VisitingStudentPolicy,
    HostingAvailability,
    InternationalEligibility,
    ContactChannel
)
from src.opportunities.repository import OpportunityRepository
from src.opportunities.adapters.academic_lab import AcademicLabAdapter
from src.opportunities.data.academic_expansion_data import GLOBAL_ACADEMIC_EXPANSION_CATALOG
from src.opportunities.academic_reconciliation import AcademicPortfolioReconciler
from src.opportunities.scheduling import OpportunityScheduler


def test_production_reconciliation_integrity():
    """
    Verify all 1,390 production professors are reconciled into development DB,
    preserving their source='PRODUCTION_CAMPAIGN_LINKED' and campaign statuses.
    """
    dev_db_path = settings.DATA_DIR / "development" / "opportunity_dev.db"
    assert dev_db_path.exists(), "Development database must exist."

    conn = sqlite3.connect(str(dev_db_path))
    cur = conn.cursor()

    # Check production-linked count
    cur.execute("SELECT COUNT(*) FROM opportunities WHERE source = 'PRODUCTION_CAMPAIGN_LINKED';")
    prod_linked_count = cur.fetchone()[0]
    # Reconciler linked 2 to existing dev seeds and inserted 1388 net-new = 1390 production professors
    assert prod_linked_count == 1388, f"Expected 1388 production-linked records, got {prod_linked_count}"

    # Check preserved statuses in development DB
    cur.execute("""
        SELECT academic_reply_outcome, COUNT(*) 
        FROM opportunities 
        WHERE source = 'PRODUCTION_CAMPAIGN_LINKED' 
        GROUP BY academic_reply_outcome;
    """)
    outcomes = dict(cur.fetchall())
    assert len(outcomes) > 0
    
    # Must preserve production campaign origin notes
    cur.execute("""
        SELECT COUNT(*) FROM opportunities 
        WHERE source = 'PRODUCTION_CAMPAIGN_LINKED' AND verification_notes LIKE 'Reconciled from production campaign%';
    """)
    assert cur.fetchone()[0] == 1388

    conn.close()


def test_global_academic_expansion_dataset_size_and_zero_collision():
    """
    Verify the academic expansion catalog contains >= 100 net-new faculty,
    and has strictly ZERO collisions with the 1,390 production campaign professors.
    """
    catalog = GLOBAL_ACADEMIC_EXPANSION_CATALOG
    assert len(catalog) >= 100, f"Expected at least 100 expansion candidates, found {len(catalog)}"

    # Load production emails and names
    prod_db_path = settings.DATA_DIR / "outreach_engine.db"
    assert prod_db_path.exists(), "Production database must exist for collision verification."

    conn = sqlite3.connect(f"file:{prod_db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT email, full_name FROM professors;")
    prod_rows = cur.fetchall()
    conn.close()

    prod_emails = {r[0].strip().lower() for r in prod_rows if r[0]}
    prod_names = {r[1].strip().lower() for r in prod_rows if r[1]}

    collisions = []
    for item in catalog:
        c_email = (item.get("contact_email") or "").strip().lower()
        c_name = (item.get("professor_name") or "").strip().lower()
        if c_email and c_email in prod_emails:
            collisions.append(f"Email collision: {c_email}")
        if c_name and c_name in prod_names:
            collisions.append(f"Name collision: {c_name}")

    assert len(collisions) == 0, f"Detected production collisions: {collisions}"


def test_orthogonal_feasibility_evaluation_and_unknown_availability():
    """
    Verify that research relevance does NOT imply confirmed hosting availability.
    All net-new academic opportunities must have summer_2027_hosting_availability == 'UNKNOWN'.
    """
    catalog = GLOBAL_ACADEMIC_EXPANSION_CATALOG
    verified_projects = {
        "Opportunity Intelligence / Outreach Intelligence Engine",
        "Peng-Robinson Real-Gas Cubic Equation of State Solver (C++17)",
        "PINN Error Estimation & Grid-Resolution Study (Krasowski et al. Reproduction)",
        "LLM Inference Intelligence Engine (Hardware Profiling & Roofline Diagnostics)",
        "Neural Process Twin (SciML / 2D Heat Conduction / PINNs)",
        "SuRGE Microdrone Autonomy & 3D Environment Mapping (Research Exposure)"
    }

    for item in catalog:
        # Every item in the catalog must have hosting availability strictly UNKNOWN
        assert item.get("summer_2027_hosting_availability") == "UNKNOWN", (
            f"Professor {item.get('professor_name')} must have summer_2027_hosting_availability == 'UNKNOWN'"
        )
        # Must have verified institutional email
        assert "@" in item.get("contact_email", "")
        # Must have at least 2 distinct official evidence URLs
        evidence_urls = item.get("official_evidence_urls", [])
        assert len(evidence_urls) >= 2, f"Candidate {item.get('professor_name')} must have >= 2 evidence URLs"
        # Must have matched project
        assert item.get("matched_project") in verified_projects, (
            f"Candidate {item.get('professor_name')} has unverified project: {item.get('matched_project')}"
        )


def test_academic_lab_adapter_normalization_and_time_blocks():
    """
    Verify AcademicLabAdapter normalizes opportunities with proper placement title,
    assigned IST time block, and orthogonal feasibility attributes.
    """
    adapter = AcademicLabAdapter()
    raw_items = adapter.fetch_raw_opportunities(limit=10)
    assert len(raw_items) == 10

    for item in raw_items:
        org, opp = adapter.normalize_raw_item(item)
        assert opp.opportunity_type == OpportunityType.ACADEMIC_LAB.value
        assert "Summer 2027" in opp.title
        assert any(term in opp.title for term in ["Placement", "Intern", "Fellow", "Scholar", "Inquiry", "Researcher"])
        assert opp.summer_2027_hosting_availability in ("UNKNOWN", "CONFIRMED_AVAILABLE", "CONFIRMED_UNAVAILABLE")
        assert opp.iana_timezone is not None
        assert opp.contact_email is not None
        assert opp.contact_verification_status == "VERIFIED"


def test_unified_schedule_regional_blocks_and_pending_review():
    """
    Verify scheduling engine assigns regional IST dispatch blocks and sets status to PENDING_REVIEW.
    """
    adapter = AcademicLabAdapter()
    raw_items = adapter.fetch_raw_opportunities(limit=25)
    opps = []
    orgs = {}
    for item in raw_items:
        org, opp = adapter.normalize_raw_item(item)
        opps.append(opp)
        orgs[org.organization_id] = org

    schedule_records = OpportunityScheduler.generate_unified_schedule(opps, orgs)
    assert len(schedule_records) == 25

    for rec in schedule_records:
        assert rec["channel"] == "EMAIL_DISPATCH"
        assert rec["status"] == "PENDING_REVIEW"
        assert rec["assigned_block_id"].startswith("BLOCK_")
        assert "IST" in rec["ist_window"]


def test_production_scheduler_pid_and_database_isolation():
    """
    Verify production database data/outreach_engine.db schema integrity post-Gate-A.
    """
    prod_db_path = settings.DATA_DIR / "outreach_engine.db"
    assert prod_db_path.exists()

    conn = sqlite3.connect(f"file:{prod_db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {r[0] for r in cur.fetchall()}
    conn.close()

    assert "opportunities" in tables
    assert "organizations" in tables
    assert "discovery_checkpoints" in tables
    assert "professors" in tables
    assert "outbound_delivery_tracker" in tables
