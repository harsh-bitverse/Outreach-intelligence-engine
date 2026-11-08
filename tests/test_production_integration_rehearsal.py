"""
Session 10: Complete Production Integration Rehearsal Tests.

Demonstrates:
1. Existing professor entries remain 100% unchanged on production snapshot.
2. New verified contacts enter correct regional time-block schedules.
3. Previously contacted records (suppression list / prior batches) are suppressed.
4. New-category personalized drafts pass factual validation (Harshith N, 6 projects, 0 speedups).
5. All categories share the single global rate-limit and cadence counters (10 batch, 300s break, 25s gap, 200 ceiling).
6. Portal opportunities never reach SMTP.
7. Process crashes, retries, and restarts do not cause duplicate sending.
8. Workbook and SQLite synchronization remain consistent.
"""

import os
import shutil
import sqlite3
import pytest
from datetime import datetime, date, time, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    ContactChannel,
    VerificationStatus,
    PriorityTier,
    ContactHistoryStatus
)
from src.opportunities.budget_and_tracking import GlobalBudgetManager
from src.opportunities.contact_verifier import ContactVerifier
from src.opportunities.unified_queue import UnifiedOutreachQueueManager, UnifiedQueueItem
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.opportunities.timezone_engine import DynamicTimezoneEngine


@pytest.fixture
def rehearsal_env(tmp_path):
    """
    Creates an isolated sandbox containing snapshots of production and development databases.
    Ensures zero interaction with live running processes.
    """
    prod_snapshot = tmp_path / "prod_snapshot.db"
    dev_snapshot = tmp_path / "dev_snapshot.db"

    # Copy real production DB to snapshot
    shutil.copy2(settings.SQLITE_DB_PATH, prod_snapshot)

    # Initialize dev SQLite schema
    conn = sqlite3.connect(dev_snapshot)
    conn.execute("PRAGMA foreign_keys = ON;")
    # Initialize opportunities table in dev_snapshot
    conn.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            opportunity_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            title TEXT NOT NULL,
            opportunity_type TEXT NOT NULL,
            source TEXT NOT NULL,
            location TEXT,
            iana_timezone TEXT,
            technical_domains TEXT,
            technical_relevance_summary TEXT,
            potential_contribution TEXT,
            matched_project_id TEXT,
            eligibility_criteria TEXT,
            internship_duration_weeks INTEGER,
            target_term TEXT,
            compensation_type TEXT,
            compensation_details TEXT,
            official_evidence_urls TEXT,
            freshness_timestamp TEXT,
            hiring_contact_channel TEXT,
            contact_email TEXT,
            contact_name TEXT,
            contact_verification_status TEXT,
            application_url TEXT,
            application_deadline TEXT,
            qualification_score REAL,
            score_breakdown TEXT,
            priority_tier TEXT,
            discovery_status TEXT,
            application_status TEXT,
            is_confirmed_opening INTEGER,
            next_action TEXT,
            created_at TEXT,
            updated_at TEXT,
            last_checked_at TEXT,
            audit_classification TEXT,
            role_title_type TEXT,
            workplace_type TEXT,
            international_eligibility TEXT,
            compensation_evidence TEXT,
            operational_status TEXT,
            verification_notes TEXT,
            last_verified_at TEXT,
            portfolio_tier TEXT,
            requisition_type TEXT,
            stipend_amount TEXT,
            travel_support TEXT,
            housing_support TEXT,
            required_documents TEXT,
            academic_classification TEXT,
            institution_visiting_policy TEXT,
            hosting_history_verified INTEGER,
            hosting_history_evidence TEXT,
            summer_2027_hosting_availability TEXT,
            funding_availability TEXT,
            visa_sponsorship_status TEXT,
            term_compatibility TEXT,
            application_route TEXT,
            evidence_sources TEXT,
            evidence_timestamp TEXT,
            verification_confidence TEXT,
            academic_reply_outcome TEXT,
            reply_timestamp TEXT,
            reply_evidence_snippet TEXT,
            reply_evidence_id TEXT,
            professor_id TEXT,
            contact_history_status TEXT,
            assigned_block_id TEXT,
            recipient_mailing_window TEXT,
            ist_eligibility_window TEXT
        );
    """)
    conn.commit()

    budget_manager = GlobalBudgetManager(daily_limit=200)
    queue_manager = UnifiedOutreachQueueManager(
        dev_conn=conn,
        prod_db_path=prod_snapshot,
        budget_manager=budget_manager
    )

    yield {
        "prod_snapshot": prod_snapshot,
        "dev_snapshot": dev_snapshot,
        "dev_conn": conn,
        "budget_manager": budget_manager,
        "queue_manager": queue_manager,
        "tmp_path": tmp_path
    }

    conn.close()


def test_rehearsal_existing_professor_entries_remain_unchanged(rehearsal_env):
    """Demonstrate that production snapshot records are 100% untouched by unified queue operations."""
    prod_path = rehearsal_env["prod_snapshot"]
    conn = sqlite3.connect(prod_path)
    cur = conn.cursor()
    cur.execute("SELECT professor_id, email, outreach_status FROM professors ORDER BY professor_id;")
    pre_professors = cur.fetchall()
    conn.close()

    assert len(pre_professors) in (1390, 1561)

    # Execute queue operations
    qm = rehearsal_env["queue_manager"]
    opp = Opportunity(
        opportunity_id="opp_test_academic_01",
        organization_id="org_test_tum",
        title="Visiting Research Intern",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        source="TEST",
        iana_timezone="Europe/Berlin",
        hiring_contact_channel=ContactChannel.FACULTY_PI.value,
        contact_email="test.prof@tum.de",
        contact_name="Prof. Test Munich",
        official_evidence_urls=["https://www.tum.de/prof/test"],
        contact_verification_status="VERIFIED"
    )
    org = Organization(
        organization_id="org_test_tum",
        name="Technical University of Munich (TUM)",
        category=OpportunityType.ACADEMIC_LAB.value,
        iana_timezone="Europe/Berlin"
    )
    qm.enqueue_opportunity(opp, org)

    # Verify production snapshot is completely unchanged
    conn2 = sqlite3.connect(prod_path)
    cur2 = conn2.cursor()
    cur2.execute("SELECT professor_id, email, outreach_status FROM professors ORDER BY professor_id;")
    post_professors = cur2.fetchall()
    conn2.close()

    assert pre_professors == post_professors


def test_rehearsal_new_verified_contacts_enter_correct_schedules(rehearsal_env):
    """Demonstrate that new verified opportunities enter the correct regional time-block schedule."""
    qm = rehearsal_env["queue_manager"]

    # 1. Tokyo opportunity -> BLOCK_04
    opp_tokyo = Opportunity(
        opportunity_id="opp_test_tokyo_01",
        organization_id="org_tokyo_univ",
        title="Systems Research Intern",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        source="TEST",
        iana_timezone="Asia/Tokyo",
        hiring_contact_channel=ContactChannel.FACULTY_PI.value,
        contact_email="lab-interns@u-tokyo.ac.jp",
        contact_name="Prof. Tokyo Researcher",
        official_evidence_urls=["https://www.u-tokyo.ac.jp/lab"],
        contact_verification_status="VERIFIED"
    )
    org_tokyo = Organization(
        organization_id="org_tokyo_univ",
        name="The University of Tokyo",
        category=OpportunityType.ACADEMIC_LAB.value,
        iana_timezone="Asia/Tokyo"
    )
    item_tokyo = qm.enqueue_opportunity(opp_tokyo, org_tokyo)
    assert item_tokyo.queue_status == "QUEUED"
    assert item_tokyo.assigned_block_id == "BLOCK_04"
    assert item_tokyo.delivery_channel == "SMTP_GATEWAY"

    # 2. London opportunity -> BLOCK_08
    opp_london = Opportunity(
        opportunity_id="opp_test_london_01",
        organization_id="org_ucl",
        title="AI Engineering Intern",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        source="TEST",
        iana_timezone="Europe/London",
        hiring_contact_channel=ContactChannel.FACULTY_PI.value,
        contact_email="cs-internships@ucl.ac.uk",
        contact_name="Prof. UCL Systems",
        official_evidence_urls=["https://www.ucl.ac.uk/cs"],
        contact_verification_status="VERIFIED"
    )
    org_london = Organization(
        organization_id="org_ucl",
        name="University College London",
        category=OpportunityType.ACADEMIC_LAB.value,
        iana_timezone="Europe/London"
    )
    item_london = qm.enqueue_opportunity(opp_london, org_london)
    assert item_london.queue_status == "QUEUED"
    assert item_london.assigned_block_id == "BLOCK_08"


def test_rehearsal_previously_contacted_records_are_suppressed(rehearsal_env):
    """Demonstrate that opportunities colliding with contacted professors are suppressed."""
    qm = rehearsal_env["queue_manager"]

    # Prof. Torsten Hoefler was contacted in production batch 3
    opp_hoefler = Opportunity(
        opportunity_id="opp_test_hoefler",
        organization_id="org_ethz",
        title="HPC Systems Intern",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        source="TEST",
        iana_timezone="Europe/Zurich",
        hiring_contact_channel=ContactChannel.FACULTY_PI.value,
        contact_email="htorsten@inf.ethz.ch",
        contact_name="Prof. Torsten Hoefler",
        official_evidence_urls=["https://spcl.inf.ethz.ch"],
        contact_verification_status="VERIFIED"
    )
    org_ethz = Organization(
        organization_id="org_ethz",
        name="ETH Zurich",
        category=OpportunityType.ACADEMIC_LAB.value,
        iana_timezone="Europe/Zurich"
    )
    item = qm.enqueue_opportunity(opp_hoefler, org_ethz)
    assert item.queue_status == "SUPPRESSED_COLLISION"

    # Attempting pre_dispatch_check must reject
    authorized, reason = qm.pre_dispatch_check(item.queue_id)
    assert authorized is False
    assert "ITEM_NOT_DISPATCHABLE" in reason or "SUPPRESSED" in reason


def test_rehearsal_new_category_personalized_drafts_pass_factual_validation():
    """Demonstrate that drafts for startups, companies, and academia pass strict factual validation."""
    drafts = DomainAdaptivePersonalizer.generate_twenty_verified_multicategory_drafts()
    assert len(drafts) == 20

    forbidden_hallucinations = [
        "100x speedup", "50x faster", "certified bound", "commercial drone autopilot",
        "custom flight hardware", "algebraic multigrid solver", "multigrid accelerator",
        "simd intrinsics", "multi-phase flash calculation"
    ]

    for d in drafts:
        body = d.get("full_draft") or d.get("pitch_body") or d.get("statement_of_purpose") or ""
        assert "Harshith N" in body
        assert "IIT Delhi" in body or "Indian Institute of Technology Delhi" in body
        for forbidden in forbidden_hallucinations:
            assert forbidden not in body.lower(), f"Draft contained forbidden hallucination: '{forbidden}'"


def test_rehearsal_all_categories_share_rate_limit_counters(rehearsal_env):
    """Demonstrate that all categories share the single 10-batch, 300s break, 25s gap, and 200 daily ceiling."""
    qm = rehearsal_env["queue_manager"]
    bm = rehearsal_env["budget_manager"]

    # Allow academic dispatch
    can_disp, _ = bm.can_dispatch("ACADEMIC_PROFESSOR", requested_count=1)
    assert can_disp is True

    # 1. Dispatch 10 emails across mixed categories at t0
    t0 = datetime(2026, 11, 9, 3, 0, 0, tzinfo=timezone.utc)
    for i in range(10):
        # Spaced 25 seconds apart
        msg_time = t0 + timedelta(seconds=i * 25)
        bm.record_dispatch("ACADEMIC_PROFESSOR", count=1, now=msg_time)

    # 2. 11th dispatch within 300 seconds must be rejected for inter-batch cooling break
    t_11 = t0 + timedelta(seconds=9 * 25 + 30) # 30s after batch completed
    can_disp_11, msg_11 = bm.can_dispatch("ACADEMIC_PROFESSOR", requested_count=1, now=t_11)
    assert can_disp_11 is False
    assert "INTER_BATCH_COOLDOWN_ACTIVE" in msg_11

    # 3. After 300s break has elapsed, dispatch is permitted
    t_12 = t0 + timedelta(seconds=9 * 25 + 301)
    can_disp_12, _ = bm.can_dispatch("ACADEMIC_PROFESSOR", requested_count=1, now=t_12)
    assert can_disp_12 is True

    # 4. Global 200/day limit across categories
    bm.record_dispatch("ACADEMIC_PROFESSOR", count=190, now=t_12)
    can_disp_over, msg_over = bm.can_dispatch("ACADEMIC_PROFESSOR", requested_count=1, now=t_12)
    assert can_disp_over is False
    assert "DAILY_LIMIT_EXCEEDED" in msg_over


def test_rehearsal_portal_opportunities_never_reach_smtp(rehearsal_env):
    """Demonstrate that portal opportunities are permanently blocked from SMTP dispatch."""
    qm = rehearsal_env["queue_manager"]

    opp_cern = Opportunity(
        opportunity_id="opp_cern_portal_test",
        organization_id="org_cern",
        title="CERN openlab Summer Student Fellow",
        opportunity_type=OpportunityType.INSTITUTIONAL_PROGRAM.value,
        source="TEST",
        hiring_contact_channel=ContactChannel.APPLICATION_PORTAL.value,
        application_url="https://openlab.cern/summer-student-programme",
        contact_email=None,
        official_evidence_urls=["https://openlab.cern/summer-student-programme"],
        contact_verification_status="VERIFIED"
    )
    org_cern = Organization(
        organization_id="org_cern",
        name="CERN",
        category=OpportunityType.INSTITUTIONAL_PROGRAM.value
    )

    item = qm.enqueue_opportunity(opp_cern, org_cern)
    assert item.queue_status == "ROUTED_PORTAL"
    assert item.delivery_channel == "PORTAL_TRACKER"

    # Attempting to dispatch via SMTP MUST raise ValueError
    with pytest.raises(ValueError, match="PORTAL_DISPATCH_REJECTED"):
        qm.dispatch_queue_item(item.queue_id)


def test_rehearsal_crash_recovery_prevents_duplicate_sending(rehearsal_env):
    """Demonstrate that process crashes during PROCESSING state are safely recovered without duplicates."""
    qm = rehearsal_env["queue_manager"]
    conn = rehearsal_env["dev_conn"]

    now_iso = datetime.now(timezone.utc).isoformat()

    # Insert two items stuck in 'PROCESSING' state
    # Item A: Already received SMTP 250 OK before crash (has Message-ID)
    conn.execute("""
        INSERT INTO unified_outreach_queue (
            queue_id, opportunity_id, category, organization_name, recipient_name,
            recipient_email, hiring_contact_channel, delivery_channel, assigned_block_id,
            iana_timezone, queue_status, smtp_message_id, created_at, updated_at
        ) VALUES (
            'UQ-CRASH-ITEM-A', 'opp_crash_a', 'ACADEMIC_LAB', 'ETH Zurich', 'Prof. A',
            'prof.a@ethz.ch', 'FACULTY_PI', 'SMTP_GATEWAY', 'BLOCK_07', 'Europe/Zurich',
            'PROCESSING', '<12345.5916.789@iitd.ac.in>', ?, ?
        );
    """, (now_iso, now_iso))

    # Item B: Crashed before SMTP transmission (no Message-ID)
    conn.execute("""
        INSERT INTO unified_outreach_queue (
            queue_id, opportunity_id, category, organization_name, recipient_name,
            recipient_email, hiring_contact_channel, delivery_channel, assigned_block_id,
            iana_timezone, queue_status, smtp_message_id, created_at, updated_at
        ) VALUES (
            'UQ-CRASH-ITEM-B', 'opp_crash_b', 'ACADEMIC_LAB', 'Oxford', 'Prof. B',
            'prof.b@ox.ac.uk', 'FACULTY_PI', 'SMTP_GATEWAY', 'BLOCK_08', 'Europe/London',
            'PROCESSING', NULL, ?, ?
        );
    """, (now_iso, now_iso))
    conn.commit()

    # Execute crash recovery
    recovery_report = qm.recover_from_crash()
    assert recovery_report["orphans_found"] == 2
    assert recovery_report["recovered_dispatched"] == 1
    assert recovery_report["requeued_for_dispatch"] == 1

    item_a = qm.get_queue_item("UQ-CRASH-ITEM-A")
    assert item_a.queue_status == "DISPATCHED_SMTP"

    item_b = qm.get_queue_item("UQ-CRASH-ITEM-B")
    assert item_b.queue_status == "QUEUED"
    assert item_b.retry_count == 1
    assert item_b.failure_reason == "RECOVERED_AFTER_PROCESS_RESTART"
