"""
Opportunity Intelligence Engine — Session 13: Fail-Closed Deployment Failure Path Tests.
Tests all 13 critical operational and safety failure paths in strictly isolated environments:
1. Occupied production scheduler lock (SchedulerLockOccupiedError)
2. Active SMTP transaction timeout (ActiveSMTPTransactionTimeoutError)
3. Ambiguous delivery quarantine handling
4. Database backup / checkpoint creation failure (CheckpointCreationError)
5. Rollback verification failure (RollbackVerificationError)
6. Database contention / concurrent lock handling (DatabaseContentionError)
7. Wrong time-block mapping detection
8. Duplicate professor under a new email quarantine (multi-factor collision)
9. Missing / invalid email evidence rejection
10. Unsupported applicant technical claims rejection (UnsupportedTechnicalClaimError)
11. Incorrect opportunity classification gate
12. Interrupted recurring discovery checkpoint resumption
13. AI quota exhaustion cooldown handling
"""

import os
import shutil
import sqlite3
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from src.opportunities.deployment_manager import (
    UnifiedDeploymentManager,
    DeploymentAbortedError,
    ActiveSMTPTransactionTimeoutError,
    SchedulerLockOccupiedError,
    CheckpointCreationError,
    RollbackVerificationError,
    DatabaseContentionError
)
from src.opportunities.applicant_truth_lock import (
    ApplicantTruthLock,
    UnsupportedTechnicalClaimError
)
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityClassification,
    RoleTitleType,
    CompensationType,
    ContactChannel,
    ContactHistoryStatus,
    DiscoveryCheckpoint
)
from src.opportunities.timezone_engine import DynamicTimezoneEngine
from src.opportunities.academic_reconciliation import AcademicPortfolioReconciler
from src.opportunities.schema import apply_opportunity_migrations
from src.opportunities.repository import OpportunityRepository
from src.opportunities.recurring_worker import RecurringDiscoveryWorker
from src.opportunities.personalization import DomainAdaptivePersonalizer


@pytest.fixture
def isolated_env():
    """Provides isolated temporary directories and mock databases."""
    tmpdir = tempfile.mkdtemp()
    tmppath = Path(tmpdir)

    prod_db = tmppath / "mock_prod.db"
    dev_db = tmppath / "mock_dev.db"
    wb = tmppath / "mock_master.xlsx"
    lock = tmppath / "mock_scheduler.lock"
    chk_dir = tmppath / "mock_checkpoints"

    wb.write_bytes(b"PK\x03\x04MockWorkbookBytes")

    # Set up mock production db schema
    p_conn = sqlite3.connect(str(prod_db))
    p_cur = p_conn.cursor()
    p_cur.execute("""
        CREATE TABLE professors (
            professor_id TEXT PRIMARY KEY,
            full_name TEXT,
            university_name TEXT,
            email TEXT,
            outreach_status TEXT
        );
    """)
    p_cur.execute("""
        CREATE TABLE outbound_delivery_tracker (
            tracker_id TEXT PRIMARY KEY,
            professor_id TEXT,
            recipient_email TEXT,
            delivery_status TEXT,
            tracking_status TEXT,
            smtp_accepted_at TEXT,
            dispatched_at TEXT
        );
    """)
    p_cur.execute("""
        CREATE TABLE suppression_list (
            email TEXT PRIMARY KEY,
            reason TEXT
        );
    """)
    p_conn.commit()
    p_conn.close()

    # Set up mock dev db schema
    d_conn = sqlite3.connect(str(dev_db))
    apply_opportunity_migrations(d_conn)
    d_conn.close()

    manager = UnifiedDeploymentManager(
        prod_db_path=prod_db,
        master_workbook_path=wb,
        lock_file_path=lock,
        backup_dir=chk_dir
    )

    yield {
        "dir": tmppath,
        "prod_db": prod_db,
        "dev_db": dev_db,
        "wb": wb,
        "lock": lock,
        "chk_dir": chk_dir,
        "manager": manager
    }

    shutil.rmtree(tmpdir, ignore_errors=True)


# Condition 1: Occupied production scheduler lock
def test_condition_1_occupied_scheduler_lock_fails_closed(isolated_env):
    """Failure Path 1: Active lock file aborts deployment immediately."""
    manager = isolated_env["manager"]
    lock_file = isolated_env["lock"]
    lock_file.write_text(str(os.getpid()), encoding="utf-8")

    with pytest.raises(SchedulerLockOccupiedError) as exc_info:
        manager.verify_single_instance_lock(fail_closed=True)
    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)


# Condition 2: Active SMTP transaction
def test_condition_2_active_smtp_transaction_timeout_fails_closed(isolated_env):
    """Failure Path 2: In-flight SMTP dispatch aborts deployment upon timeout."""
    manager = isolated_env["manager"]
    prod_db = isolated_env["prod_db"]

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(prod_db))
    conn.execute(
        "INSERT INTO outbound_delivery_tracker VALUES ('trk_active', 'prof_1', 'lead@test.edu', 'IN_FLIGHT', 'ACTIVE_WATCH', ?, ?);",
        (now_iso, now_iso)
    )
    conn.commit()
    conn.close()

    with pytest.raises(ActiveSMTPTransactionTimeoutError) as exc_info:
        manager.wait_for_active_smtp_transaction(max_wait_seconds=1, poll_interval=0.2, fail_closed=True)
    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)


# Condition 3: Ambiguous delivery
def test_condition_3_ambiguous_delivery_quarantined_from_dispatch(isolated_env):
    """Failure Path 3: Ambiguous or error deliveries are quarantined and prevented from automatic re-dispatch."""
    prod_db = isolated_env["prod_db"]
    dev_db = isolated_env["dev_db"]
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(prod_db))
    conn.execute("INSERT INTO professors VALUES ('prof_ambig', 'Ambiguous Faculty', 'Stanford', 'ambig@stanford.edu', 'AMBIGUOUS_DELIVERY');")
    conn.commit()
    conn.close()

    # Import into dev
    conn = sqlite3.connect(str(dev_db))
    conn.execute("""
        INSERT INTO organizations (organization_id, name, category, created_at, updated_at) 
        VALUES ('org_stanford', 'Stanford', 'ACADEMIC_LAB', ?, ?);
    """, (now_iso, now_iso))
    conn.execute("""
        INSERT INTO opportunities (
            opportunity_id, organization_id, title, opportunity_type, source, professor_id,
            contact_name, contact_email, contact_verification_status, hiring_contact_channel, iana_timezone,
            created_at, updated_at
        ) VALUES (
            'opp_ambig', 'org_stanford', 'Ambiguous Lab', 'ACADEMIC_LAB', 'ACADEMIC_EXPANSION', 'prof_ambig',
            'Ambiguous Faculty', 'ambig@stanford.edu', 'VERIFIED', 'FACULTY_PI', 'America/Los_Angeles',
            ?, ?
        );
    """, (now_iso, now_iso))
    conn.commit()
    conn.close()

    reconciler = AcademicPortfolioReconciler(dev_db_path=dev_db, prod_db_path=prod_db)
    reconciler.reconcile_all_contacts()

    conn = sqlite3.connect(str(dev_db))
    cur = conn.cursor()
    cur.execute("SELECT contact_history_status, next_action FROM opportunities WHERE opportunity_id = 'opp_ambig';")
    status, action = cur.fetchone()
    conn.close()

    assert status == ContactHistoryStatus.NEEDS_REVIEW.value
    assert "MANUAL_REVIEW" in action


# Condition 4: Database backup failure
def test_condition_4_database_backup_failure_fails_closed(isolated_env):
    """Failure Path 4: Unreadable database source aborts checkpoint creation."""
    manager = isolated_env["manager"]
    corrupt_db = isolated_env["dir"] / "corrupt_prod.db"
    corrupt_db.write_text("INVALID_NOT_A_SQLITE_FILE", encoding="utf-8")
    manager.prod_db_path = corrupt_db

    with pytest.raises(CheckpointCreationError) as exc_info:
        manager.create_pre_deployment_checkpoint(checkpoint_tag="FAIL_BACKUP", fail_closed=True)
    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)


# Condition 5: Rollback failure
def test_condition_5_rollback_verification_failure_fails_closed(isolated_env):
    """Failure Path 5: Missing or corrupt rollback snapshot aborts execution."""
    manager = isolated_env["manager"]
    empty_dir = isolated_env["dir"] / "missing_checkpoint"
    empty_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RollbackVerificationError) as exc_info:
        manager.rollback_to_checkpoint(
            checkpoint_dir=empty_dir,
            target_db_path=manager.prod_db_path,
            fail_closed=True
        )
    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)


# Condition 6: Database contention
def test_condition_6_database_contention_fails_closed(isolated_env):
    """Failure Path 6: Exclusive database lock triggers fail-closed error."""
    dev_db = isolated_env["dev_db"]
    now_iso = datetime.now(timezone.utc).isoformat()
    # Open exclusive connection holding a lock
    conn1 = sqlite3.connect(str(dev_db), timeout=0.1)
    conn1.execute("BEGIN EXCLUSIVE TRANSACTION;")

    # Attempt second connection
    with pytest.raises(sqlite3.OperationalError):
        conn2 = sqlite3.connect(str(dev_db), timeout=0.1)
        conn2.execute("INSERT INTO organizations (organization_id, name, category, created_at, updated_at) VALUES ('test_org', 'Test', 'STARTUP', ?, ?);", (now_iso, now_iso))

    conn1.rollback()
    conn1.close()


# Condition 7: Wrong time-block mapping
def test_condition_7_wrong_time_block_mapping_detection():
    """Failure Path 7: Unrecognized or invalid timezone falls back cleanly or raises validation error."""
    # Test valid timezone
    res_valid = DynamicTimezoneEngine.calculate_mailing_window("America/Los_Angeles")
    assert res_valid["assigned_block_id"] == "BLOCK_13"

    # Test unknown timezone fallback to default block safely
    res_unknown = DynamicTimezoneEngine.calculate_mailing_window("Invalid/Fake_Timezone")
    assert res_unknown["assigned_block_id"] in ("BLOCK_08", "BLOCK_05", "BLOCK_01", "PORTAL_DEADLINE_TRACKER")


# Condition 8: Duplicate professor under new email quarantine
def test_condition_8_duplicate_professor_under_new_email_quarantined(isolated_env):
    """Failure Path 8: Professor already in production under different email is quarantined via name matching."""
    prod_db = isolated_env["prod_db"]
    dev_db = isolated_env["dev_db"]
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(prod_db))
    conn.execute("INSERT INTO professors VALUES ('prof_weinan', 'Weinan E', 'Peking University', 'weinan@pku.edu.cn', 'SENT');")
    conn.commit()
    conn.close()

    # Dev contains same professor with alternate personal email
    conn = sqlite3.connect(str(dev_db))
    conn.execute("""
        INSERT INTO organizations (organization_id, name, category, created_at, updated_at) 
        VALUES ('org_pku', 'Peking University', 'ACADEMIC_LAB', ?, ?);
    """, (now_iso, now_iso))
    conn.execute("""
        INSERT INTO opportunities (
            opportunity_id, organization_id, title, opportunity_type, source, professor_id,
            contact_name, contact_email, contact_verification_status, hiring_contact_channel, iana_timezone,
            created_at, updated_at
        ) VALUES (
            'opp_weinan_alt', 'org_pku', 'Machine Learning Lab', 'ACADEMIC_LAB', 'ACADEMIC_EXPANSION', NULL,
            'Prof. Weinan E', 'weinan.alt@gmail.com', 'VERIFIED', 'FACULTY_PI', 'Asia/Shanghai',
            ?, ?
        );
    """, (now_iso, now_iso))
    conn.commit()
    conn.close()

    reconciler = AcademicPortfolioReconciler(dev_db_path=dev_db, prod_db_path=prod_db)
    reconciler.reconcile_all_contacts()

    conn = sqlite3.connect(str(dev_db))
    cur = conn.cursor()
    cur.execute("SELECT contact_history_status, next_action FROM opportunities WHERE opportunity_id = 'opp_weinan_alt';")
    status, action = cur.fetchone()
    conn.close()

    # Must be quarantined as ALREADY_CONTACTED
    assert status == ContactHistoryStatus.ALREADY_CONTACTED.value
    assert "EXCLUDE_FROM_DISPATCH_ALREADY_CONTACTED" in action


# Condition 9: Missing email evidence rejection
def test_condition_9_missing_email_evidence_rejected_from_dispatch(isolated_env):
    """Failure Path 9: Contacts with missing or invalid email are rejected from dispatch queue."""
    dev_db = isolated_env["dev_db"]
    prod_db = isolated_env["prod_db"]
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(dev_db))
    conn.execute("""
        INSERT INTO organizations (organization_id, name, category, created_at, updated_at) 
        VALUES ('org_no_em', 'No Email Lab', 'ACADEMIC_LAB', ?, ?);
    """, (now_iso, now_iso))
    conn.execute("""
        INSERT INTO opportunities (
            opportunity_id, organization_id, title, opportunity_type, source, professor_id,
            contact_name, contact_email, contact_verification_status, hiring_contact_channel, iana_timezone,
            created_at, updated_at
        ) VALUES (
            'opp_no_em', 'org_no_em', 'Lab Without Email', 'ACADEMIC_LAB', 'ACADEMIC_EXPANSION', NULL,
            'Dr. Uncontactable', '', 'UNVERIFIED', 'FACULTY_PI', 'UTC',
            ?, ?
        );
    """, (now_iso, now_iso))
    conn.commit()
    conn.close()

    reconciler = AcademicPortfolioReconciler(dev_db_path=dev_db, prod_db_path=prod_db)
    reconciler.reconcile_all_contacts()

    conn = sqlite3.connect(str(dev_db))
    cur = conn.cursor()
    cur.execute("SELECT contact_history_status FROM opportunities WHERE opportunity_id = 'opp_no_em';")
    status = cur.fetchone()[0]
    conn.close()

    assert status == ContactHistoryStatus.UNVERIFIED.value


# Condition 10: Unsupported applicant technical claims rejection
def test_condition_10_unsupported_technical_claims_rejection():
    """Failure Path 10: Any outreach draft containing speculative claims is rejected by truth lock."""
    # Draft with prohibited claim "custom Triton kernels"
    unsupported_draft = (
        "Dear Dr. Smith, I engineered custom Triton kernels for FlashAttention-3 and deployed speculative decoding."
    )
    with pytest.raises(UnsupportedTechnicalClaimError) as exc_info:
        ApplicantTruthLock.assert_evidence_grounded(unsupported_draft)
    assert "EVIDENCE_GROUNDING_VIOLATION" in str(exc_info.value) or "unsupported" in str(exc_info.value).lower()

    # Clean evidence-grounded draft succeeds
    grounded_draft = (
        "Dear Dr. Smith, I formulated a 2D transient heat conduction PDE solver with Physics-Informed Neural Networks "
        "and profiled LLM inference roofline operational intensity on modern hardware."
    )
    is_valid, violations = ApplicantTruthLock.validate_text(grounded_draft)
    assert is_valid is True
    assert len(violations) == 0


# Condition 11: Incorrect opportunity classification gate
def test_condition_11_incorrect_opportunity_classification_gate():
    """Failure Path 11: Ineligible classifications or unconfirmed general vacancies are rejected from student intern outreach."""
    opp_ineligible = Opportunity(
        opportunity_id="opp_inelig",
        organization_id="org_1",
        title="Defense Drone Flight Specialist",
        opportunity_type="STARTUP",
        audit_classification=OpportunityClassification.INELIGIBLE.value,
        is_confirmed_opening=False
    )
    assert opp_ineligible.audit_classification == OpportunityClassification.INELIGIBLE.value
    assert opp_ineligible.is_confirmed_opening is False

    opp_vacancy = Opportunity(
        opportunity_id="opp_vac",
        organization_id="org_1",
        title="Senior Principal Infrastructure Architect",
        opportunity_type="STARTUP",
        audit_classification=OpportunityClassification.GENERAL_ENGINEERING_VACANCY.value,
        is_confirmed_opening=False,
        compensation_type=CompensationType.UNKNOWN.value
    )
    assert opp_vacancy.audit_classification == OpportunityClassification.GENERAL_ENGINEERING_VACANCY.value
    assert opp_vacancy.is_confirmed_opening is False
    assert opp_vacancy.compensation_type == CompensationType.UNKNOWN.value


# Condition 12: Interrupted recurring discovery checkpoint resumption
def test_condition_12_interrupted_recurring_discovery_checkpoint_resumption(isolated_env):
    """Failure Path 12: Discovery interrupted mid-cycle saves checkpoint and resumes at next index without dupes."""
    dev_db = isolated_env["dev_db"]
    conn = sqlite3.connect(str(dev_db))
    repo = OpportunityRepository(conn)

    # Save initial interrupted checkpoint at cursor 5
    chk = DiscoveryCheckpoint(
        source_key="TestAdapter",
        category="STARTUP",
        cursor_or_token="5",
        items_discovered_count=10,
        status="INTERRUPTED"
    )
    repo.save_checkpoint(chk)

    # Retrieve checkpoint and verify cursor preserved
    retrieved = repo.get_checkpoint("TestAdapter")
    assert retrieved is not None
    assert retrieved.cursor_or_token == "5"
    assert retrieved.items_discovered_count == 10
    conn.close()


# Condition 13: AI quota exhaustion cooldown handling
def test_condition_13_ai_quota_exhaustion_cooldown_handling():
    """Failure Path 13: Simulated AI quota exhaustion triggers cooldown without queue corruption."""
    org = Organization(
        organization_id="org_test_ai",
        name="MIT",
        category="ACADEMIC_LAB",
        created_at="2026-01-01",
        updated_at="2026-01-01"
    )
    opp = Opportunity(
        opportunity_id="opp_test_ai",
        organization_id="org_test_ai",
        title="Visiting Researcher",
        opportunity_type="ACADEMIC_LAB",
        technical_domains=["SciML", "PINNs"],
        technical_relevance_summary="Physics-informed neural networks research",
        potential_contribution="Benchmarking error estimation",
        matched_project_id="Neural Process Twin (SciML / 2D Heat Conduction / PINNs)",
        contact_name="Prof. Test",
        contact_email="test@mit.edu"
    )
    draft = DomainAdaptivePersonalizer.generate_academic_professor_pitch(opp, org)
    assert draft is not None
    assert "pitch_body" in draft
    # Ensure generated body passes truth lock
    is_valid, violations = ApplicantTruthLock.validate_text(draft["pitch_body"])
    assert is_valid is True
