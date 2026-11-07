"""
Session 15 Authoritative Integration & Verification Test Suite.

Covers:
1. Single Authoritative Workbook Migration Package (18 sheets, baseline preservation, row parity, rollback)
2. Applicant Truth Lock (Prohibition of fabricated hardware projects, strict CV grounding, 7 canonical projects)
3. Faculty Reaudit & QS Discovery Integrity (Torsten Hoefler alias detection, Session 14 collision audit, Session 15 expansion)
4. Portal Application Engine & 8 Safety Stops (Compatibility tiers, safety stop evaluation, checkpoint resumption, mock tagging)
5. Response Intelligence & Production Reconciler (31 replies, Bodner at MIT interview classification, idempotent checkpoints)
6. Concurrency Locking & Shared Global Budget (Single active sender lock, multi-category shared ceiling)
"""

import os
import json
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timezone
import openpyxl

from config.settings import settings
from src.opportunities.models import Opportunity, Organization, OpportunityType
from src.opportunities.applicant_truth_lock import ApplicantTruthLock
from src.opportunities.authoritative_migration_package import AuthoritativeWorkbookMigrationPackage
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.opportunities.portal_automation import (
    PortalApplicationDriver,
    RealPortalFormInspector,
    PortalType,
    PortalCompatibilityTier,
    ApplicationStage,
    PortalSubmissionStatus,
    BlockingReason
)
from src.opportunities.response_intelligence import (
    ResponseIntelligenceEngine,
    ProductionOutcomeReconciler,
    ReplyIntent
)
from src.opportunities.data.expansion_batch_session14 import NEW_ACADEMIC_FACULTY_SESSION14
from src.opportunities.data.expansion_batch_session15 import NEW_ACADEMIC_FACULTY_SESSION15
from src.opportunities.reconciliation_audit import ReconciliationAuditor
from src.opportunities.budget_and_tracking import GlobalBudgetManager
from src.opportunities.unified_campaign_runner import UnifiedCampaignRunner


# -----------------------------------------------------------------------------
# 1. Authoritative Workbook Migration Package
# -----------------------------------------------------------------------------
def test_authoritative_workbook_rehearsal_and_baseline_preservation(tmp_path):
    """Verify that migration rehearsal against an isolated snapshot preserves baseline sheets and achieves row parity."""
    prod_wb_path = settings.DATA_DIR / "private" / "Summer_2027_Professor_Database_WITH_TIME_BLOCKS_EXPORT.xlsx"
    assert prod_wb_path.exists(), "Master production workbook must exist"

    staging_path = tmp_path / "rehearsal_master.xlsx"
    migrator = AuthoritativeWorkbookMigrationPackage(
        prod_workbook_path=prod_wb_path,
        backup_dir=tmp_path / "backups"
    )

    rehearsal_res = migrator.execute_isolated_rehearsal(staging_output_path=staging_path)
    assert rehearsal_res["status"] == "REHEARSAL_SUCCESS"
    assert rehearsal_res["all_18_sheets_present"] is True
    assert rehearsal_res["rollback_verified"] is True

    # Baseline sheets row count check
    baseline = rehearsal_res["baseline_stats"]
    assert baseline["Professor Database"] == 1391  # 1 header + 1,390 professors
    assert baseline["University Database"] == 122   # 1 header + 121 universities
    assert baseline["Time Blocks & Distribution"] == 1433 # 1 header + 1,432 rows

    # Row parity across opportunity sheets
    parity = rehearsal_res["parity_audit"]
    assert parity["Startups"]["parity_preserved"] is True
    assert parity["Companies & Industrial R&D"]["parity_preserved"] is True
    assert parity["Institutional & National Labs"]["parity_preserved"] is True


# -----------------------------------------------------------------------------
# 2. Applicant Truth Lock & Evidence Grounding
# -----------------------------------------------------------------------------
def test_applicant_truth_lock_forbids_fabricated_hardware_accelerator():
    """Verify that ApplicantTruthLock strictly forbids tinyml-accelerator-analysis and fabricated hardware designs."""
    bad_draft = (
        "In my prior research, I developed the tinyml-accelerator-analysis custom hardware accelerator "
        "and fabricated hardware benchmark for edge devices."
    )
    is_valid, violation = ApplicantTruthLock.validate_draft_claims(bad_draft)
    assert is_valid is False
    assert "tinyml-accelerator-analysis" in violation
    assert "prohibited" in violation.lower()


def test_applicant_truth_lock_accepts_verified_canonical_projects():
    """Verify that ApplicantTruthLock allows valid claims grounded in the 7 verified projects."""
    good_draft = (
        "I am an undergraduate in Chemical Engineering at IIT Delhi. In my research project (Neural Process Twin), "
        "I developed a learned surrogate simulation pipeline in PyTorch to approximate steady-state physical fields. "
        "Additionally, I built an LLM inference evaluation harness profiling latency across quantization levels."
    )
    is_valid, violation = ApplicantTruthLock.validate_draft_claims(good_draft)
    assert is_valid is True
    assert violation is None


# -----------------------------------------------------------------------------
# 3. Faculty Reaudit & QS Discovery Integrity
# -----------------------------------------------------------------------------
def test_torsten_hoefler_identified_as_historical_alias():
    """Verify that Torsten Hoefler under torsten.hoefler@inf.ethz.ch is classified as HISTORICAL_CONTACT_ALIAS / EXISTING_PRODUCTION_PROFESSOR."""
    torsten = next(p for p in NEW_ACADEMIC_FACULTY_SESSION14 if "Torsten Hoefler" in p["professor_name"])
    assert torsten["discovery_status"] in ("HISTORICAL_CONTACT_ALIAS", "EXISTING_PRODUCTION_PROFESSOR")


def test_session14_faculty_audit_yields_exact_four_net_new_leads():
    """Audit of Session 14 professors against 1,390 production professors confirms exactly 4 net-new leads and 8 collisions."""
    net_new = [p for p in NEW_ACADEMIC_FACULTY_SESSION14 if p["discovery_status"] == "NEW_UNIQUE_FACULTY"]
    prod_collisions = [p for p in NEW_ACADEMIC_FACULTY_SESSION14 if p["discovery_status"] == "EXISTING_PRODUCTION_PROFESSOR"]
    assert len(net_new) == 4
    assert len(prod_collisions) == 8


def test_session15_expansion_batch_is_100_percent_collision_free():
    """Verify all 12 Session 15 faculty are net-new and do not collide with production professors."""
    prod_db = Path("data/outreach_engine.db")
    conn = sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT LOWER(email), LOWER(full_name) FROM professors;")
    prod_records = cur.fetchall()
    conn.close()

    prod_emails = {r[0] for r in prod_records if r[0]}
    prod_names = {r[1] for r in prod_records if r[1]}

    for fac in NEW_ACADEMIC_FACULTY_SESSION15:
        email = fac["email"].strip().lower()
        name = fac["professor_name"].strip().lower()
        assert email not in prod_emails, f"Session 15 faculty {email} must not exist in production database"
        assert name not in prod_names, f"Session 15 faculty {name} must not exist in production database"


def test_mathematical_partition_exactness_after_session15_ingestion():
    """Verify the development SQLite database maintains 0 discrepancy delta across all 1,857 records."""
    auditor = ReconciliationAuditor()
    res = auditor.run_full_reconciliation()
    assert res["total_opportunities"] == 1857
    assert res["mathematical_invariants"]["discrepancy_delta"] == 0
    assert res["mathematical_invariants"]["partition_is_exact"] is True


# -----------------------------------------------------------------------------
# 4. Portal Application Engine & Safety Stops
# -----------------------------------------------------------------------------
def test_portal_compatibility_tiers():
    """Verify ATS compatibility tier classification (Greenhouse/Ashby/Lever vs Workday/Custom)."""
    assert RealPortalFormInspector.get_compatibility_tier("https://boards.greenhouse.io/cohere/jobs/123") == PortalCompatibilityTier.BROWSER_TESTED_COMPATIBLE
    assert RealPortalFormInspector.get_compatibility_tier("https://jobs.ashbyhq.com/mistral/456") == PortalCompatibilityTier.BROWSER_TESTED_COMPATIBLE
    assert RealPortalFormInspector.get_compatibility_tier("https://jobs.lever.co/anthropic/789") == PortalCompatibilityTier.BROWSER_TESTED_COMPATIBLE
    assert RealPortalFormInspector.get_compatibility_tier("https://nvidia.wd5.myworkdayjobs.com/NVIDIA_External/000") == PortalCompatibilityTier.MOCK_ONLY_SUPPORT
    assert RealPortalFormInspector.get_compatibility_tier("https://custom-portal.org/apply") == PortalCompatibilityTier.MOCK_ONLY_SUPPORT


def test_all_eight_portal_safety_stops():
    """Verify that all 8 safety stop conditions are evaluated and block automated progress."""
    inspector = RealPortalFormInspector()

    # 1. CAPTCHA
    stop1 = inspector.evaluate_safety_stops({"has_captcha": True})
    assert stop1["blocking_reason"] == BlockingReason.CAPTCHA_REQUIRED

    # 2. MFA
    stop2 = inspector.evaluate_safety_stops({"requires_mfa": True})
    assert stop2["blocking_reason"] == BlockingReason.MFA_REQUIRED

    # 3. Custom Declaration
    stop3 = inspector.evaluate_safety_stops({"custom_declaration_required": True})
    assert stop3["blocking_reason"] == BlockingReason.CUSTOM_DECLARATION

    # 4. Legal Agreement
    stop4 = inspector.evaluate_safety_stops({"requires_legal_agreement": True})
    assert stop4["blocking_reason"] == BlockingReason.LEGAL_AGREEMENT

    # 5. Unavailable Documents
    stop5 = inspector.evaluate_safety_stops({"unavailable_documents_required": True})
    assert stop5["blocking_reason"] == BlockingReason.UNAVAILABLE_DOCUMENTS

    # 6. Unverified Work Auth
    stop6 = inspector.evaluate_safety_stops({"unverified_work_auth": True})
    assert stop6["blocking_reason"] == BlockingReason.UNVERIFIED_WORK_AUTHORIZATION

    # 7. Uncertain Eligibility
    stop7 = inspector.evaluate_safety_stops({"uncertain_eligibility": True})
    assert stop7["blocking_reason"] == BlockingReason.UNCERTAIN_ELIGIBILITY

    # 8. Unmapped Custom Field
    stop8 = inspector.evaluate_safety_stops({"unknown_unmapped_field": True, "unknown_field_name": "essay_prompt"})
    assert stop8["blocking_reason"] == BlockingReason.UNMAPPED_CUSTOM_FIELD


def test_portal_dry_run_tagging_and_zero_increment():
    """Verify that controlled rehearsals tag dry runs explicitly with MOCK_SIMULATED_NOT_SUBMITTED and 0 increment."""
    driver = PortalApplicationDriver()
    opp = Opportunity(
        opportunity_id="opp_test_dryrun",
        organization_id="org_test",
        title="ML Engineer Intern",
        opportunity_type=OpportunityType.STARTUP.value,
        application_url="https://jobs.lever.co/test/ml-intern"
    )
    state = driver.simulate_application_flow(opp)
    res = driver.rehearse_stage4_controlled_submission(state, operator_authorization_granted=True)

    assert res["status"] == "REHEARSAL_SUCCESS"
    assert res["submission_tag"] == "MOCK_SIMULATED_NOT_SUBMITTED"
    assert res["submitted_count_incremented"] == 0
    assert res["real_submission_executed"] is False
    assert res["evidence"]["mode"] == "MOCK_SIMULATED_NOT_SUBMITTED"


# -----------------------------------------------------------------------------
# 5. Response Intelligence & Production Reconciliation
# -----------------------------------------------------------------------------
def test_production_outcome_reconciler_read_only_and_bodner_invitation():
    """Verify read-only reconciliation of production outcomes detects Bodner at MIT interview invitation."""
    reconciler = ProductionOutcomeReconciler()
    res = reconciler.reconcile_production_outcomes()

    assert res["status"] == "RECONCILIATION_SUCCESS"
    assert res["production_read_only_verified"] is True
    assert res["total_tracker_replies"] == 31
    assert res["total_bounces_monitored"] == 94
    assert res["high_priority_positive_signals_count"] >= 1

    top_item = res["high_priority_items"][0]
    assert "bodner" in top_item["sender"].lower()
    assert top_item["classification"] == "INTERVIEW_INVITATION"
    assert top_item["action_required"] == "SCHEDULE_INTERVIEW_CALL"


# -----------------------------------------------------------------------------
# 6. Concurrency Locking & Shared Global Budget
# -----------------------------------------------------------------------------
def test_single_active_sender_lock_mutual_exclusion(tmp_path):
    """Verify that a second sender process fails closed when the lockfile is held."""
    test_lock = tmp_path / "test_active_sender.lock"
    runner1 = UnifiedCampaignRunner(lock_file_path=test_lock)
    assert runner1.acquire_lock() is True

    runner2 = UnifiedCampaignRunner(lock_file_path=test_lock)
    assert runner2.acquire_lock() is False, "Second runner must fail closed when lock held"

    runner1.release_lock()
    assert not test_lock.exists()


def test_shared_global_budget_decrement_across_categories():
    """Verify that all opportunity categories consume from the single global daily limit."""
    budget = GlobalBudgetManager(daily_limit=10)
    budget.authorize_category("STARTUP")
    budget.authorize_category("ACADEMIC_LAB")
    budget.authorize_category("FRONTIER_COMPANY")

    assert budget.remaining_budget_today() == 10
    budget.record_dispatch("ACADEMIC_LAB", count=2)
    budget.record_dispatch("STARTUP", count=3)
    budget.record_dispatch("FRONTIER_COMPANY", count=1)

    assert budget.total_dispatched_today() == 6
    assert budget.remaining_budget_today() == 4
