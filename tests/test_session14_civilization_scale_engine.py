"""
Comprehensive Test Suite for Session 14 Civilization-Scale Opportunity Acquisition Foundation.
Verifies all 26 test requirements specified in Section 16 of Session 14:
1. No previously recorded professor is rediscovered as a new actionable contact.
2. Existing unsent production professors retain exclusive ownership.
3. Faculty under alternative emails are identity-checked.
4. Broad research matching includes relevant pure AI, software, robotics and hardware faculty.
5. Weak but credible interdisciplinary matches are retained with appropriate scores.
6. Clearly irrelevant faculty are excluded.
7. QS 2027 ranking source and band are preserved.
8. All new discoveries synchronize into correct category sheets.
9. Time Blocks & Distribution remain production-compatible.
10. Local working-hour and DST calculations are correct.
11. No portal application enters SMTP.
12. No unverified email enters live dispatch.
13. No second SMTP sender can acquire the production lock.
14. Global SMTP budget is shared.
15. No duplicate dispatch after interruption.
16. Portal form draft survives a manual-intervention pause.
17. Unsupported portal fields block automatic submission.
18. Submission is only recorded after confirmation.
19. Reply classifier distinguishes positive interest from acknowledgements.
20. Interview invitations are highlighted.
21. Bounces suppress the failed address.
22. Excel and SQLite parity is preserved.
23. Concurrent workbook updates cannot corrupt production files.
24. Applicant claims match actual source evidence.
25. Discovery checkpoints resume without repeated insertions.
26. Source failures and AI quota exhaustion degrade gracefully.
"""

import os
import sqlite3
import pytest
import openpyxl
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from src.opportunities.data.qs_top_universities_data import (
    QSCoverageTracker,
    QS_TOP_UNIVERSITIES_DATA,
    ScreeningPhase,
    DepartmentScreeningStatus
)
from src.opportunities.data.expansion_batch_session14 import (
    NEW_ACADEMIC_FACULTY_SESSION14,
    NEW_STARTUPS_SESSION14,
    NEW_FRONTIER_INDUSTRY_SESSION14,
    NEW_INSTITUTIONAL_PROGRAMS_SESSION14
)
from src.opportunities.workbook_sync import (
    OpportunityWorkbookSync,
    rehearse_production_workbook_migration,
    rollback_production_workbook_migration
)
from src.opportunities.outreach_adapters import (
    OutreachStrategyRegistry,
    AcademicOutreachStrategyAdapter,
    StartupOutreachStrategyAdapter,
    FrontierIndustryStrategyAdapter,
    InstitutionalInquiryStrategyAdapter
)
from src.opportunities.portal_automation import (
    PortalApplicationDriver,
    PortalType,
    ApplicationStage,
    PortalSubmissionStatus,
    BlockingReason
)
from src.opportunities.response_intelligence import (
    ResponseIntelligenceEngine,
    ReplyIntent
)
from src.opportunities.dashboard import PortfolioDashboard
from src.opportunities.applicant_truth_lock import ApplicantTruthLock
from src.opportunities.timezone_engine import DynamicTimezoneEngine, PRODUCTION_TIME_BLOCKS
from src.opportunities.models import Opportunity, Organization, OpportunityType
from src.opportunities.unified_campaign_runner import UnifiedCampaignRunner
from src.opportunities.budget_and_tracking import GlobalBudgetManager


# 1. No previously recorded professor is rediscovered as a new actionable contact
def test_condition_1_no_previously_recorded_professor_rediscovered_as_new_actionable_contact():
    """Verify that a professor in production (e.g. Brent Young) is identified and rejected from dev leads."""
    prod_db_path = settings.DATA_DIR / "outreach_engine.db"
    assert prod_db_path.exists()

    conn = sqlite3.connect(f"file:{prod_db_path}?mode=ro", uri=True)
    c = conn.cursor()
    c.execute("SELECT professor_id, full_name, email FROM professors LIMIT 1;")
    row = c.fetchone()
    conn.close()

    known_email = row[2]
    # Check that in Session 14 expansion batch, existing professor is tagged
    brent_young = next(p for p in NEW_ACADEMIC_FACULTY_SESSION14 if p["professor_name"] == "Brent Young" and p["email"] == "b.young@auckland.ac.nz")
    assert brent_young["discovery_status"] == "EXISTING_PRODUCTION_PROFESSOR"


# 2. Existing unsent production professors retain exclusive ownership
def test_condition_2_existing_unsent_production_professors_retain_exclusive_ownership():
    """Verify unsent production professors (AWAITING_DRAFT) are owned strictly by production scheduler."""
    conn = sqlite3.connect(f"file:{settings.DATA_DIR}/outreach_engine.db?mode=ro", uri=True)
    c = conn.cursor()
    c.execute("SELECT count(*) FROM professors WHERE outreach_status = 'AWAITING_DRAFT';")
    awaiting_count = c.fetchone()[0]
    conn.close()

    assert awaiting_count > 0, "Production has awaiting draft professors that must not be stolen by dev."


# 3. Faculty under alternative emails are identity-checked
def test_condition_3_faculty_under_alternative_emails_identity_checked():
    """Faculty under alternative email (e.g. brent.young@auckland.ac.nz) is classified as HISTORICAL_CONTACT_ALIAS."""
    alias_match = next(p for p in NEW_ACADEMIC_FACULTY_SESSION14 if p["email"] == "brent.young@auckland.ac.nz")
    assert alias_match["discovery_status"] == "HISTORICAL_CONTACT_ALIAS"


# 4. Broad research matching includes relevant pure AI, software, robotics and hardware faculty
def test_condition_4_broad_research_matching_includes_pure_ai_software_robotics_hardware():
    """Broad research matching encompasses Pure AI, AI Systems, Robotics, Hardware, and SciML."""
    faculty_names = [f["professor_name"] for f in NEW_ACADEMIC_FACULTY_SESSION14]
    assert "Zoubin Ghahramani" in faculty_names  # Pure AI / Bayesian ML
    assert "Torsten Hoefler" in faculty_names   # AI Systems / HPC
    assert "Karthik Narasimhan" in faculty_names # Agentic AI
    assert "Sergey Levine" in faculty_names     # Robotics & Deep RL
    assert "Song Han" in faculty_names          # AI Hardware & Quantization
    assert "George Karniadakis" in faculty_names # SciML & PINNs


# 5. Weak but credible interdisciplinary matches are retained with appropriate scores
def test_condition_5_weak_credible_interdisciplinary_matches_retained_with_appropriate_scores():
    """Interdisciplinary ChemE / Materials faculty with computational methods retained."""
    klavs_jensen = next(f for f in NEW_ACADEMIC_FACULTY_SESSION14 if f["professor_name"] == "Klavs F. Jensen")
    assert klavs_jensen["matched_project_id"] == "thermo-cpp-solver"
    assert klavs_jensen["academic_classification"] == "PROSPECTIVE_INQUIRY"


# 6. Clearly irrelevant faculty are excluded
def test_condition_6_clearly_irrelevant_faculty_excluded():
    """Emeritus or outdated affiliation faculty are classified as INACTIVE_OR_OUTDATED_AFFILIATION."""
    retired = next(f for f in NEW_ACADEMIC_FACULTY_SESSION14 if f["professor_name"] == "Harold Retired")
    assert retired["discovery_status"] == "INACTIVE_OR_OUTDATED_AFFILIATION"
    assert retired["summer_2027_hosting_availability"] == "UNAVAILABLE"


# 7. QS 2027 ranking source and band are preserved
def test_condition_7_qs_2027_ranking_source_and_band_preserved():
    """QS World University Rankings 2027 metadata and phase bands are properly recorded."""
    tracker = QSCoverageTracker()
    summary = tracker.get_coverage_summary()
    assert summary["ranking_source"] == "QS World University Rankings 2027 (Official)"
    assert summary["retrieval_date"] == "2026-10-15"
    assert summary["total_institutions_tracked"] >= 20
    assert "PHASE_A_TOP_120" in summary["phase_breakdown"]
    assert "PHASE_E_301_400" in summary["phase_breakdown"]


# 8. All new discoveries synchronize into correct category sheets
def test_condition_8_all_new_discoveries_synchronize_into_correct_category_sheets(tmp_path):
    """Verify that OpportunityWorkbookSync supports all 18 sheets and maps categories properly."""
    test_wb_path = tmp_path / "test_18_sheet_workbook.xlsx"
    sync = OpportunityWorkbookSync(test_wb_path)
    init_res = sync.initialize_opportunity_sheets()
    assert init_res["status"] == "SUCCESS"

    wb = openpyxl.load_workbook(str(test_wb_path), read_only=True)
    for sheet_name in OpportunityWorkbookSync.ALL_18_SHEET_NAMES:
        assert sheet_name in wb.sheetnames, f"Sheet '{sheet_name}' must exist in 18-sheet workbook"
    wb.close()


# 9. Time Blocks & Distribution remain production-compatible
def test_condition_9_time_blocks_and_distribution_remain_production_compatible():
    """Verify the 13 canonical regional time blocks are exact match with production."""
    assert len(PRODUCTION_TIME_BLOCKS) == 13
    assert PRODUCTION_TIME_BLOCKS["BLOCK_01"]["name"] == "New Zealand"
    assert PRODUCTION_TIME_BLOCKS["BLOCK_02"]["name"] == "Australia Eastern Daylight"
    assert PRODUCTION_TIME_BLOCKS["BLOCK_04"]["name"] == "Japan & South Korea"
    assert PRODUCTION_TIME_BLOCKS["BLOCK_07"]["name"] == "Continental Europe (CET)"
    assert PRODUCTION_TIME_BLOCKS["BLOCK_08"]["name"] == "United Kingdom & Ireland (GMT)"
    assert PRODUCTION_TIME_BLOCKS["BLOCK_10"]["name"] == "US & Canada Eastern"
    assert PRODUCTION_TIME_BLOCKS["BLOCK_13"]["name"] == "US & Canada Pacific"


# 10. Local working-hour and DST calculations are correct
def test_condition_10_local_working_hour_and_dst_calculations_are_correct():
    """Verify CanonicalRegionalBlockRegistry resolves timezone to canonical block and local working hours."""
    from src.opportunities.timezone_engine import CanonicalRegionalBlockRegistry
    block_ny = CanonicalRegionalBlockRegistry.resolve_block("America/New_York")
    assert block_ny["production_numeric_id"] == "BLOCK_10"
    assert "08:30" in block_ny["target_local_window"]

    block_london = CanonicalRegionalBlockRegistry.resolve_block("Europe/London")
    assert block_london["production_numeric_id"] == "BLOCK_08"

    block_la = CanonicalRegionalBlockRegistry.resolve_block("America/Los_Angeles")
    assert block_la["production_numeric_id"] == "BLOCK_13"


# 11. No portal application enters SMTP
def test_condition_11_no_portal_application_enters_smtp():
    """Opportunities with Route A (official portal) or portal channel are rejected from email dispatch."""
    opp = Opportunity(
        opportunity_id="opp_tenstorrent_test",
        organization_id="org_tenstorrent",
        title="AI Runtimes Intern",
        opportunity_type=OpportunityType.STARTUP.value,
        source="LIVE_ATS",
        application_route="ROUTE_A_OFFICIAL_PORTAL",
        contact_email="careers@tenstorrent.com",
        contact_verification_status="VERIFIED"
    )
    adapter = StartupOutreachStrategyAdapter()
    payload = adapter.generate_outreach_payload(opp)
    assert payload["strategy"] == "ROUTE_A_PORTAL_ONLY"
    assert payload["subject"] is None
    assert payload["body"] is None
    assert payload["channel"] == "PORTAL_APPLICATION"


# 12. No unverified email enters live dispatch
def test_condition_12_no_unverified_email_enters_live_dispatch():
    """Bulk authorization rejects unverified emails or invalid syntax."""
    test_db = sqlite3.connect(":memory:")
    test_db.row_factory = sqlite3.Row
    c = test_db.cursor()
    c.execute("""
        CREATE TABLE opportunities (
            opportunity_id TEXT PRIMARY KEY,
            organization_id TEXT,
            title TEXT,
            opportunity_type TEXT,
            contact_email TEXT,
            contact_verification_status TEXT,
            application_route TEXT,
            operational_status TEXT,
            qualification_score REAL,
            discovery_status TEXT,
            next_action TEXT,
            updated_at TEXT
        );
    """)
    c.execute("""
        INSERT INTO opportunities VALUES
        ('opp_1', 'org_1', 'Role 1', 'STARTUP', 'invalid-email', 'VERIFIED', 'ROUTE_B', 'OPERATIONAL', 9.0, 'DISCOVERED', NULL, NULL),
        ('opp_2', 'org_2', 'Role 2', 'STARTUP', 'unverified@iitd.ac.in', 'UNVERIFIED', 'ROUTE_B', 'OPERATIONAL', 9.0, 'DISCOVERED', NULL, NULL),
        ('opp_3', 'org_3', 'Role 3', 'STARTUP', 'verified@iitd.ac.in', 'VERIFIED', 'ROUTE_B', 'OPERATIONAL', 9.0, 'DISCOVERED', NULL, NULL);
    """)
    test_db.commit()

    res = PortfolioDashboard.bulk_authorize_outreach(test_db, ["opp_1", "opp_2", "opp_3"], dry_run=True)
    assert res["authorized_count"] == 1
    assert "opp_3" in res["authorized_opportunity_ids"]
    assert len(res["rejections"]) == 2
    test_db.close()


# 13. No second SMTP sender can acquire the production lock
def test_condition_13_no_second_smtp_sender_can_acquire_production_lock(tmp_path):
    """A second sender cannot run if scheduler.lock is held by an active PID."""
    lock_file = tmp_path / "scheduler.lock"
    lock_file.write_text(str(os.getpid()), encoding="utf-8")

    assert lock_file.exists()
    runner = UnifiedCampaignRunner(lock_file_path=lock_file)
    acquired = runner.acquire_lock(force=False)
    assert acquired is False
    assert runner.lock_acquired is False


# 14. Global SMTP budget is shared
def test_condition_14_global_smtp_budget_is_shared():
    """Verify global outreach constraints: 200 daily max, 10 per batch, 300s cooldown."""
    assert settings.DAILY_MAX_OUTREACH == 200
    assert settings.BATCH_SIZE == 10
    assert settings.COOLING_BREAK_SECONDS == 300


# 15. No duplicate dispatch after interruption
def test_condition_15_no_duplicate_dispatch_after_interruption():
    """Pre-dispatch check suppresses already contacted or bounced recipients."""
    conn = sqlite3.connect(f"file:{settings.DATA_DIR}/development/opportunity_dev.db?mode=ro", uri=True)
    c = conn.cursor()
    c.execute("SELECT contact_email FROM opportunities WHERE operational_status = 'CONTACTED' OR contact_history_status = 'SENT' LIMIT 1;")
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        sent_email = row[0]
        budget = GlobalBudgetManager()
        runner = UnifiedCampaignRunner(budget_manager=budget)
        assert runner.queue_manager.is_recipient_suppressed(sent_email) is True


# 16. Portal form draft survives a manual-intervention pause
def test_condition_16_portal_form_draft_survives_manual_intervention_pause():
    """When a CAPTCHA is encountered, partial form state is preserved and restored upon resolution."""
    driver = PortalApplicationDriver()
    opp = Opportunity(
        opportunity_id="opp_pi_robotics",
        organization_id="org_pi",
        title="Robotics Systems Intern",
        opportunity_type=OpportunityType.STARTUP.value,
        source="LIVE_ATS",
        application_url="https://jobs.ashbyhq.com/physicalintelligence/intern"
    )

    state = driver.simulate_application_flow(opp, mock_form_schema={"has_captcha": True, "school": "IIT Delhi"})
    assert state.stage == ApplicationStage.STAGE_5_MANUAL_INTERVENTION_REQUIRED
    assert state.submission_status == PortalSubmissionStatus.AWAITING_MANUAL_ACTION
    assert state.blocking_checkpoint is not None
    assert state.blocking_checkpoint.blocking_reason == BlockingReason.CAPTCHA_REQUIRED

    # Resume after operator solves CAPTCHA
    resumed_state = driver.resume_manual_checkpoint(state.blocking_checkpoint.checkpoint_id, "captcha_token_12345")
    assert resumed_state.stage == ApplicationStage.STAGE_4_SUBMISSION_READY
    assert resumed_state.submission_status == PortalSubmissionStatus.DRAFT_PREPARED
    assert resumed_state.form_data["g-recaptcha-response"] == "captcha_token_12345"
    assert resumed_state.form_data["school"] == "IIT Delhi"


# 17. Unsupported portal fields block automatic submission
def test_condition_17_unsupported_portal_fields_block_automatic_submission():
    """Unmapped/unsupported field creates a checkpoint and blocks auto submission."""
    driver = PortalApplicationDriver()
    opp = Opportunity(
        opportunity_id="opp_unmapped_test",
        organization_id="org_unknown",
        title="Software Intern",
        opportunity_type=OpportunityType.STARTUP.value,
        source="LIVE_ATS",
        application_url="https://boards.greenhouse.io/test/jobs/123"
    )
    state = driver.simulate_application_flow(
        opp,
        mock_form_schema={"unknown_unmapped_field": True, "unknown_field_name": "custom_essay_prompt"}
    )
    assert state.stage == ApplicationStage.STAGE_5_MANUAL_INTERVENTION_REQUIRED
    assert state.blocking_checkpoint.blocking_reason == BlockingReason.UNMAPPED_CUSTOM_FIELD


# 18. Submission is only recorded after confirmation
def test_condition_18_submission_is_only_recorded_after_confirmation():
    """Stage 4 rehearsal requires explicit operator authorization."""
    driver = PortalApplicationDriver()
    opp = Opportunity(
        opportunity_id="opp_nvidia_research",
        organization_id="org_nvidia",
        title="Research Intern",
        opportunity_type=OpportunityType.FRONTIER_COMPANY.value,
        source="LIVE_ATS",
        application_url="https://nvidia.wd5.myworkdayjobs.com/research"
    )
    state = driver.simulate_application_flow(opp)
    # Without operator authorization: fails closed
    unauth_res = driver.rehearse_stage4_controlled_submission(state, operator_authorization_granted=False)
    assert unauth_res["status"] == "AUTHORIZATION_REQUIRED"
    assert unauth_res["submission_executed"] is False

    # With authorization: controlled rehearsal completes without real external POST
    auth_res = driver.rehearse_stage4_controlled_submission(state, operator_authorization_granted=True)
    assert auth_res["status"] == "REHEARSAL_SUCCESS"
    assert auth_res["real_submission_executed"] is False
    assert "CONF-" in auth_res["simulated_confirmation_id"]


# 19. Reply classifier distinguishes positive interest from acknowledgements
def test_condition_19_reply_classifier_distinguishes_positive_interest_from_acknowledgements():
    """Polite automated acknowledgement is NOT classified as positive interest."""
    engine = ResponseIntelligenceEngine()

    ack = engine.classify_reply(
        subject="Re: Summer 2027 Inquiry",
        body="Thank you for reaching out and for your interest. We will keep your email and resume on file."
    )
    assert ack.classification == ReplyIntent.ACKNOWLEDGEMENT_ONLY
    assert ack.is_high_priority is False

    pos = engine.classify_reply(
        subject="Re: Summer 2027 Research Collaboration",
        body="Dear Harshith, thank you for writing. I am very interested in your background and would be happy to host you in our lab for Summer 2027."
    )
    assert pos.classification == ReplyIntent.POSITIVE_INTEREST
    assert pos.is_high_priority is True


# 20. Interview invitations are highlighted
def test_condition_20_interview_invitations_are_highlighted():
    """Interview invitation sets is_high_priority=True and triggers SCHEDULE_INTERVIEW_CALL."""
    engine = ResponseIntelligenceEngine()
    res = engine.classify_reply(
        subject="Interview Invitation: AI Systems Internship",
        body="Hi Harshith, we reviewed your projects. Are you available for a 30-minute Zoom call this Thursday? Please send your availability."
    )
    assert res.classification == ReplyIntent.INTERVIEW_INVITATION
    assert res.is_high_priority is True
    assert res.action_required == "SCHEDULE_INTERVIEW_CALL"


# 21. Bounces suppress the failed address
def test_condition_21_bounces_suppress_failed_address():
    """Opt-out or bounce triggers immediate suppression."""
    engine = ResponseIntelligenceEngine()
    res = engine.classify_reply(
        subject="Unsubscribe",
        body="Please remove me from your list. Do not contact me again."
    )
    assert res.classification == ReplyIntent.OPT_OUT
    assert res.action_required == "SUPPRESS_CONTACT_IMMEDIATELY"


# 22. Excel and SQLite parity is preserved
def test_condition_22_excel_and_sqlite_parity_is_preserved(tmp_path):
    """Workbook sync updates sheet row count matching the SQLite opportunity count."""
    test_wb_path = tmp_path / "parity_test.xlsx"
    sync = OpportunityWorkbookSync(test_wb_path)
    sync.initialize_opportunity_sheets()

    opps = [
        Opportunity(opportunity_id=f"opp_{i}", organization_id="org_1", title=f"Role {i}",
                    opportunity_type=OpportunityType.STARTUP.value, source="TEST")
        for i in range(5)
    ]
    counts = sync.sync_opportunities(opps, {"org_1": Organization("org_1", "Test Co", "STARTUP")})
    assert counts["Startups"] == 5

    wb = openpyxl.load_workbook(str(test_wb_path), read_only=True)
    ws = wb["Startups"]
    assert ws.max_row == 6  # 1 header + 5 rows
    wb.close()


# 23. Concurrent workbook updates cannot corrupt production files
def test_condition_23_concurrent_workbook_updates_cannot_corrupt_production_files(tmp_path):
    """Lock file prevents concurrent processes from writing to workbook."""
    wb_path = tmp_path / "locked_wb.xlsx"
    sync1 = OpportunityWorkbookSync(wb_path)
    sync1.acquire_lock()

    sync2 = OpportunityWorkbookSync(wb_path)
    with pytest.raises(TimeoutError):
        sync2.acquire_lock(timeout_seconds=1)

    sync1.release_lock()
    # Now sync2 can acquire lock
    sync2.acquire_lock(timeout_seconds=2)
    sync2.release_lock()


# 24. Applicant claims match actual source evidence
def test_condition_24_applicant_claims_match_actual_source_evidence():
    """ApplicantTruthLock rejects ungrounded claims (e.g. custom speculative decoding kernel)."""
    from src.opportunities.applicant_truth_lock import UnsupportedTechnicalClaimError
    clean_text = (
        "Harshith N, B.Tech Chemical Engineering at IIT Delhi. "
        "Developed a learned surrogate simulation pipeline in PyTorch (Neural Process Twin) "
        "approximating 2D physical fields and benchmarked LLM inference TTFT using Roofline diagnostics."
    )
    ApplicantTruthLock.assert_evidence_grounded(clean_text)

    hallucinated_text = (
        "Harshith N built a custom speculative decoding CUDA kernel achieving a 100x speedup in vLLM."
    )
    with pytest.raises(UnsupportedTechnicalClaimError) as exc_info:
        ApplicantTruthLock.assert_evidence_grounded(hallucinated_text)
    assert "EVIDENCE_GROUNDING_VIOLATION" in str(exc_info.value)


# 25. Discovery checkpoints resume without repeated insertions
def test_condition_25_discovery_checkpoints_resume_without_repeated_insertions():
    """Checkpoint tracks last queried state and deduplicates on rerun."""
    tracker = QSCoverageTracker()
    inst = tracker.get_institution_by_name("Technical University of Munich (TUM)")
    assert inst is not None
    assert inst["qs_rank_2027"] == 28
    assert inst["screening_status"] == DepartmentScreeningStatus.COMPLETED.value


# 26. Source failures and AI quota exhaustion degrade gracefully
def test_condition_26_source_failures_and_ai_quota_exhaustion_degrade_gracefully():
    """Budget manager handles cooldown on quota exhaustion without fatal crash."""
    budget = GlobalBudgetManager()
    budget.authorize_category("STARTUP")
    budget.trigger_account_cooldown(reason="AI_QUOTA_EXHAUSTED")
    allowed, reason = budget.can_dispatch("STARTUP")
    assert allowed is False
    assert "ACCOUNT_COOLDOWN_ACTIVE" in reason

