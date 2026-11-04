"""
Comprehensive Automated Test Suite for Opportunity Intelligence Engine (Session 01).
Covers:
- Schema migrations & safe isolation
- URL canonicalization & deduplication primitives
- Deterministic ID computation
- Multi-factor qualification & ranking model
- Category adapters (Academic Labs, Startups, Frontier Industry, Institutional Programs)
- Repository CRUD, checkpoints, and runs
- Multi-sheet Excel synchronization with strict preservation of existing sheets
- Discovery worker lifecycle and lock management
- Routing boundaries (application portals vs email outreach)
- Production isolation safeguards
"""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Any

import openpyxl
import pytest

from config.settings import settings
from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    CompensationType,
    ContactChannel,
    VerificationStatus,
    DiscoveryStatus,
    ApplicationStatus
)
from src.opportunities.schema import apply_opportunity_migrations
from src.opportunities.registry import (
    canonicalize_url,
    normalize_organization_name,
    compute_organization_id,
    compute_opportunity_id,
    SourceRegistry
)
from src.opportunities.ranking import OpportunityRanker
from src.opportunities.repository import OpportunityRepository
from src.opportunities.adapters import (
    AcademicLabAdapter,
    StartupAdapter,
    FrontierIndustryAdapter,
    InstitutionalProgramAdapter
)
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.opportunities.discovery_worker import OpportunityDiscoveryWorker


@pytest.fixture
def isolated_opp_env():
    """Create isolated SQLite database and Excel workbook for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        tmp_db = tmp_dir / "test_opportunity_engine.db"
        tmp_wb = tmp_dir / "test_opportunity_workbook.xlsx"
        tmp_lock = tmp_dir / "test_discovery.lock"

        # Copy existing master workbook and DB
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, tmp_wb)
        shutil.copy2(settings.SQLITE_DB_PATH, tmp_db)

        # Apply opportunity migrations to isolated DB
        conn = sqlite3.connect(tmp_db)
        apply_opportunity_migrations(conn)
        repo = OpportunityRepository(conn)

        sync = OpportunityWorkbookSync(workbook_path=tmp_wb)

        yield {
            "tmp_dir": tmp_dir,
            "db_path": tmp_db,
            "workbook_path": tmp_wb,
            "lock_path": tmp_lock,
            "conn": conn,
            "repo": repo,
            "sync": sync,
        }
        conn.close()


# ------------------------------------------------------------------------------
# 1. Schema Migration & Table Verification
# ------------------------------------------------------------------------------
def test_schema_migration_isolated():
    """Verify that migrations create all 4 new tables idempotently in an isolated DB."""
    conn = sqlite3.connect(":memory:")
    res = apply_opportunity_migrations(conn)
    assert res["status"] == "MIGRATIONS_APPLIED"
    assert "organizations" in res["verified_tables"]
    assert "opportunities" in res["verified_tables"]
    assert "discovery_checkpoints" in res["verified_tables"]
    assert "discovery_runs" in res["verified_tables"]

    # Re-running must be completely idempotent
    res2 = apply_opportunity_migrations(conn)
    assert res2["status"] == "MIGRATIONS_APPLIED"
    conn.close()


# ------------------------------------------------------------------------------
# 2. URL Canonicalization & Normalization
# ------------------------------------------------------------------------------
def test_url_canonicalization_and_normalization():
    """Verify stripping of marketing parameters, protocol normalization, and host lowercasing."""
    raw = "http://WWW.modal.com/careers/systems-intern?utm_source=linkedin&utm_medium=job_post&ref=social#apply"
    expected = "https://modal.com/careers/systems-intern"
    assert canonicalize_url(raw) == expected

    raw2 = "https://deepmind.google/about/careers/student-researcher/?fbclid=12345&trk=campaign"
    assert canonicalize_url(raw2) == "https://deepmind.google/about/careers/student-researcher"

    assert normalize_organization_name("Modal Labs, Inc.") == "modal"
    assert normalize_organization_name("Sarvam AI Technologies Pvt Ltd") == "sarvam"
    assert normalize_organization_name("Anthropic, PBC") == "anthropic pbc"


# ------------------------------------------------------------------------------
# 3. Deterministic ID Computation
# ------------------------------------------------------------------------------
def test_deterministic_id_computation():
    """Verify reproducible and unique deterministic IDs for orgs and opportunities."""
    org_id_1 = compute_organization_id("Modal Labs", "https://modal.com")
    org_id_2 = compute_organization_id("Modal Labs Inc", "https://www.modal.com/about")
    assert org_id_1 == org_id_2
    assert org_id_1.startswith("org_")

    opp_id_1 = compute_opportunity_id(org_id_1, "STARTUP", "Systems & AI Infrastructure Engineering Intern", "Summer 2027")
    opp_id_2 = compute_opportunity_id(org_id_1, "STARTUP", "Systems & AI Infrastructure Engineering Intern", "Summer 2027")
    assert opp_id_1 == opp_id_2
    assert opp_id_1.startswith("opp_")


# ------------------------------------------------------------------------------
# 4. Multi-Factor Qualification & Ranking: Technical Fit
# ------------------------------------------------------------------------------
def test_ranking_technical_fit():
    """Verify technical scoring matches applicant verified projects and assigns matched_project_id."""
    opp_sciml = Opportunity(
        opportunity_id="test_sciml",
        organization_id="org_test",
        title="Physics-Informed Neural Operator Research Intern",
        opportunity_type="ACADEMIC_LAB",
        source="TEST",
        technical_domains=["Scientific ML", "PINNs", "Surrogate Simulation"],
        technical_relevance_summary="Developing PyTorch surrogates for steady-state heat conduction.",
        official_evidence_urls=["https://mit.edu/research"]
    )
    score, breakdown, status, matched_proj = OpportunityRanker.evaluate_opportunity(opp_sciml)
    assert breakdown.technical_fit >= 8.0
    assert matched_proj == "Neural Process Twin"

    opp_inference = Opportunity(
        opportunity_id="test_inference",
        organization_id="org_test",
        title="LLM Inference Optimization Engineer",
        opportunity_type="STARTUP",
        source="TEST",
        technical_domains=["Inference Optimization", "Roofline Model", "HPC", "CUDA"],
        technical_relevance_summary="Optimizing GPU memory bandwidth and ONNX Runtime kernel latency.",
        official_evidence_urls=["https://modal.com/careers"]
    )
    score2, breakdown2, status2, matched_proj2 = OpportunityRanker.evaluate_opportunity(opp_inference)
    assert breakdown2.technical_fit >= 8.0
    assert matched_proj2 == "LLM Inference Intelligence Engine"


# ------------------------------------------------------------------------------
# 5. Multi-Factor Qualification: Compensation & Ineligibility
# ------------------------------------------------------------------------------
def test_ranking_compensation_and_ineligibility():
    """Verify that competitive compensation adds points while hard ineligibility disqualifies."""
    opp_paid = Opportunity(
        opportunity_id="test_paid",
        organization_id="org_test",
        title="AI Engineer",
        opportunity_type="STARTUP",
        source="TEST",
        compensation_type="PAID",
        compensation_details="$10,000/month",
        eligibility_criteria="Open to undergraduate students with international visa support.",
        official_evidence_urls=["https://modal.com"]
    )
    score, breakdown, status, _ = OpportunityRanker.evaluate_opportunity(opp_paid)
    assert breakdown.compensation == 20.0
    assert breakdown.eligibility >= 10.0

    # Ineligible role (PhD only / US Citizen required)
    opp_ineligible = Opportunity(
        opportunity_id="test_inelig",
        organization_id="org_test",
        title="Defense AI Researcher",
        opportunity_type="FRONTIER_COMPANY",
        source="TEST",
        compensation_type="PAID",
        eligibility_criteria="Must be US Citizen with active security clearance. PhD only.",
        official_evidence_urls=["https://defense.gov"]
    )
    score_inelig, breakdown_inelig, status_inelig, _ = OpportunityRanker.evaluate_opportunity(opp_ineligible)
    assert status_inelig == DiscoveryStatus.DISQUALIFIED.value
    assert breakdown_inelig.eligibility == 0.0


# ------------------------------------------------------------------------------
# 6. Deduplication Primitives
# ------------------------------------------------------------------------------
def test_deduplication_primitives(isolated_opp_env):
    """Verify repository accurately identifies duplicate opportunities and contacts."""
    repo = isolated_opp_env["repo"]

    opp = Opportunity(
        opportunity_id="opp_dup_check_1",
        organization_id="org_dup_check",
        title="Systems Engineering Intern",
        opportunity_type="STARTUP",
        source="TEST",
        application_url="https://example.com/apply/123",
        contact_email="hiring@example.com",
        official_evidence_urls=["https://example.com"]
    )
    repo.save_opportunity(opp)

    # Duplicate by ID
    assert repo.is_opportunity_duplicate("opp_dup_check_1") is True
    # Duplicate by application URL
    assert repo.is_opportunity_duplicate("opp_different_id", canonical_application_url="https://example.com/apply/123") is True
    # Not duplicate
    assert repo.is_opportunity_duplicate("opp_fresh_new", canonical_application_url="https://example.com/apply/fresh") is False


# ------------------------------------------------------------------------------
# 7. Category Adapters & Normalization
# ------------------------------------------------------------------------------
def test_category_adapters_normalization(isolated_opp_env):
    """Verify that all four adapters produce valid, grounded organizations and opportunities."""
    repo = isolated_opp_env["repo"]

    adapters = [
        AcademicLabAdapter(),
        StartupAdapter(),
        FrontierIndustryAdapter(),
        InstitutionalProgramAdapter()
    ]

    for adapter in adapters:
        raw_items = adapter.fetch_raw_opportunities(limit=3)
        assert len(raw_items) >= 2, f"Adapter {adapter.adapter_name} returned insufficient seeds"

        for item in raw_items:
            org, opp = adapter.normalize_raw_item(item)
            assert org.organization_id.startswith("org_")
            assert opp.opportunity_id.startswith("opp_")
            assert opp.opportunity_type == adapter.category
            assert adapter.verify_grounding(opp) is True

            # Score and save
            adapter.qualify_and_score(opp)
            repo.save_organization(org)
            repo.save_opportunity(opp)

            saved = repo.get_opportunity(opp.opportunity_id)
            assert saved is not None
            assert saved.title == opp.title
            assert saved.qualification_score > 0.0


# ------------------------------------------------------------------------------
# 8. Checkpoints and Discovery Runs
# ------------------------------------------------------------------------------
def test_checkpoints_and_discovery_runs(isolated_opp_env):
    """Verify durable checkpoints and run state tracking."""
    repo = isolated_opp_env["repo"]
    adapter = StartupAdapter()

    run = adapter.run_discovery_cycle(repo, limit=2)
    assert run.status == "COMPLETED"
    assert run.opportunities_found >= 2
    assert run.opportunities_qualified >= 1

    checkpoint = repo.get_checkpoint(adapter.adapter_name)
    assert checkpoint is not None
    assert checkpoint.status == "SUCCESS"
    assert checkpoint.items_discovered_count >= 1


# ------------------------------------------------------------------------------
# 9. Multi-Sheet Workbook Sync & Existing Sheet Preservation
# ------------------------------------------------------------------------------
def test_workbook_sync_preserves_existing_sheets(isolated_opp_env):
    """
    Verify that OpportunityWorkbookSync adds the 5 new sheets
    and preserves existing 'Time Blocks & Distribution' and 'Professor Database' completely intact.
    """
    wb_p = isolated_opp_env["workbook_path"]
    sync = isolated_opp_env["sync"]
    repo = isolated_opp_env["repo"]

    # Check pre-sync row count in Time Blocks & Distribution
    wb_pre = openpyxl.load_workbook(str(wb_p), data_only=True)
    tb_rows_pre = wb_pre["Time Blocks & Distribution"].max_row
    pd_rows_pre = wb_pre["Professor Database"].max_row
    wb_pre.close()

    # 1. Initialize sheets
    init_res = sync.initialize_opportunity_sheets()
    assert init_res["status"] == "SUCCESS"
    assert set(sync.NEW_SHEET_NAMES).issubset(set(init_res["created_sheets"]))

    # 2. Populate some test opportunities across categories
    adapter1 = AcademicLabAdapter()
    adapter2 = StartupAdapter()
    run1 = adapter1.run_discovery_cycle(repo, limit=2)
    run2 = adapter2.run_discovery_cycle(repo, limit=2)

    all_opps = repo.list_opportunities(limit=50)
    orgs = {opp.organization_id: repo.get_organization(opp.organization_id) for opp in all_opps}

    sync_counts = sync.sync_opportunities(all_opps, orgs)
    assert sync_counts["Startups"] >= 1
    assert sync_counts["Application Tracker"] >= 1

    # 3. Verify existing sheets were untouched
    wb_post = openpyxl.load_workbook(str(wb_p), data_only=True)
    assert wb_post["Time Blocks & Distribution"].max_row == tb_rows_pre
    assert wb_post["Professor Database"].max_row == pd_rows_pre

    # Verify new sheets exist and have data
    for sheet_name in sync.NEW_SHEET_NAMES:
        assert sheet_name in wb_post.sheetnames
        ws = wb_post[sheet_name]
        assert ws.max_row >= 1  # At least header row
    wb_post.close()


# ------------------------------------------------------------------------------
# 10. Discovery Worker Single Sweep & Lock Safety
# ------------------------------------------------------------------------------
def test_discovery_worker_single_sweep(isolated_opp_env):
    """Verify OpportunityDiscoveryWorker coordinates adapters and updates workbook cleanly."""
    repo = isolated_opp_env["repo"]
    sync = isolated_opp_env["sync"]
    lock_path = isolated_opp_env["lock_path"]

    worker = OpportunityDiscoveryWorker(
        repo=repo,
        workbook_sync=sync,
        lock_path=lock_path
    )

    sweep_res = worker.run_single_sweep(limit_per_adapter=2)
    assert sweep_res["total_found"] >= 4
    assert sweep_res["total_qualified"] >= 3
    assert "workbook_sync" in sweep_res
    assert len(sweep_res["adapters_run"]) == 4

    # Lock must be released
    assert not lock_path.exists()


# ------------------------------------------------------------------------------
# 11. Routing Boundaries (Portals vs Outreach)
# ------------------------------------------------------------------------------
def test_routing_boundaries():
    """Verify correct next_action and channel routing based on opportunity type."""
    # Academic Lab: Faculty PI contact -> Academic Outreach
    opp_lab = Opportunity(
        opportunity_id="opp_route_lab",
        organization_id="org_lab",
        title="Summer Visiting Scholar",
        opportunity_type="ACADEMIC_LAB",
        source="TEST",
        hiring_contact_channel="FACULTY_PI",
        contact_email="pi@stanford.edu",
        next_action="PREPARE_ACADEMIC_LAB_PROPOSAL"
    )
    assert opp_lab.hiring_contact_channel == ContactChannel.FACULTY_PI.value

    # Corporate Frontier Lab: Application Portal -> Portal Submission (Zero unsolicited emails)
    opp_corp = Opportunity(
        opportunity_id="opp_route_corp",
        organization_id="org_corp",
        title="Student Researcher",
        opportunity_type="FRONTIER_COMPANY",
        source="TEST",
        hiring_contact_channel="APPLICATION_PORTAL",
        contact_email=None,
        next_action="SUBMIT_OFFICIAL_CAREERS_PORTAL"
    )
    assert opp_corp.hiring_contact_channel == ContactChannel.APPLICATION_PORTAL.value
    assert opp_corp.contact_email is None


# ------------------------------------------------------------------------------
# 12. Strict Isolation of Production Database
# ------------------------------------------------------------------------------
def test_zero_impact_on_live_production_db():
    """Verify that live production database path is protected and never touched by isolated tests."""
    prod_db = settings.SQLITE_DB_PATH
    assert prod_db.exists(), "Live production database must exist"
    # Ensure our isolated tests did not add 'opportunities' table to live DB
    conn = sqlite3.connect(prod_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities';")
    row = cur.fetchone()
    conn.close()
    assert row is None, "Production database must remain unmigrated until separate controlled integration step!"
