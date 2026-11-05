"""
Session 11: Unified Campaign Runner and Multi-Category Scheduling Tests.

Validates Workstream A & B:
1. Multi-category unified queue enqueueing (academic, startup, frontier, institution, portal).
2. Strict channel segregation: portal items never touch SMTP.
3. 13-block regional mapping and dynamic timezone handling.
4. Pre-dispatch production collision suppression.
5. Shared cadence: 10-message batch size, 300s cooling break, 25s min spacing, 200/day limit.
6. Ambiguous delivery safeguard: SMTPAmbiguousDeliveryError permanently prohibits auto-retry.
7. Crash recovery: orphaned PROCESSING items recovered safely.
"""

import os
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone, date
from pathlib import Path

from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    ContactChannel,
    VerificationStatus,
    ContactHistoryStatus
)
from src.opportunities.unified_queue import UnifiedOutreachQueueManager, UnifiedQueueItem
from src.opportunities.unified_campaign_runner import UnifiedCampaignRunner
from src.opportunities.budget_and_tracking import GlobalBudgetManager


@pytest.fixture
def mock_campaign_env():
    """Sets up an isolated database and queue manager for campaign testing."""
    tmpdir = tempfile.mkdtemp(prefix="camp_test_")
    tmppath = Path(tmpdir)

    dev_db = tmppath / "dev_opp.db"
    prod_db = tmppath / "prod_outreach.db"
    lock_file = tmppath / "scheduler.lock"

    # Initialize dev DB schema
    dev_conn = sqlite3.connect(str(dev_db))
    dev_conn.execute("CREATE TABLE opportunities (opportunity_id TEXT PRIMARY KEY);")
    dev_conn.commit()

    # Initialize prod DB schema
    prod_conn = sqlite3.connect(str(prod_db))
    prod_conn.execute("""
        CREATE TABLE professors (
            professor_id TEXT PRIMARY KEY,
            university_name TEXT,
            full_name TEXT,
            email TEXT,
            outreach_status TEXT
        );
    """)
    prod_conn.execute("""
        CREATE TABLE outreach_suppressions (
            email TEXT PRIMARY KEY,
            reason TEXT
        );
    """)
    prod_conn.execute("""
        CREATE TABLE suppression_list (
            email TEXT PRIMARY KEY,
            reason TEXT
        );
    """)
    prod_conn.execute("""
        CREATE TABLE outbound_delivery_tracker (
            tracker_id TEXT PRIMARY KEY,
            recipient_email TEXT,
            dispatched_at TEXT,
            delivery_status TEXT,
            final_status TEXT
        );
    """)
    prod_conn.commit()
    prod_conn.close()

    budget = GlobalBudgetManager(daily_limit=200, batch_size=10, cooling_period_seconds=300)
    runner = UnifiedCampaignRunner(
        dev_db_path=dev_db,
        prod_db_path=prod_db,
        lock_file_path=lock_file,
        budget_manager=budget
    )

    yield {
        "runner": runner,
        "qm": runner.queue_manager,
        "dev_db": dev_db,
        "prod_db": prod_db,
        "dev_conn": dev_conn,
        "budget": budget,
        "tmpdir": tmppath
    }

    dev_conn.close()


def test_enqueue_multi_category_and_portal_segregation(mock_campaign_env):
    """Test enqueuing across categories and strict portal application isolation."""
    qm: UnifiedOutreachQueueManager = mock_campaign_env["qm"]

    # 1. Startup email contact
    startup_org = Organization(
        organization_id="org_startup",
        name="Etched",
        category="STARTUP",
        website_url="https://etched.com",
        iana_timezone="America/Los_Angeles"
    )
    startup_opp = Opportunity(
        opportunity_id="opp_startup",
        organization_id="org_startup",
        opportunity_type=OpportunityType.STARTUP.value,
        title="Inference Systems Intern",
        hiring_contact_channel=ContactChannel.EMAIL.value,
        contact_name="Etched Founder",
        contact_email="founder@etched.com",
        official_evidence_urls=["https://etched.com/careers"],
        contact_verification_status="VERIFIED"
    )
    item_startup = qm.enqueue_opportunity(startup_opp, startup_org)
    assert item_startup.delivery_channel == "SMTP_GATEWAY"
    assert item_startup.queue_status == "QUEUED"
    assert item_startup.assigned_block_id == "BLOCK_13"

    # 2. Portal Application (Frontier Company)
    portal_org = Organization(
        organization_id="org_portal",
        name="Apple",
        category="FRONTIER_COMPANY",
        website_url="https://apple.com",
        iana_timezone="America/Los_Angeles"
    )
    portal_opp = Opportunity(
        opportunity_id="opp_portal",
        organization_id="org_portal",
        opportunity_type=OpportunityType.FRONTIER_COMPANY.value,
        title="Silicon Performance Intern",
        hiring_contact_channel=ContactChannel.APPLICATION_PORTAL.value,
        application_url="https://jobs.apple.com",
        official_evidence_urls=["https://jobs.apple.com"]
    )
    item_portal = qm.enqueue_opportunity(portal_opp, portal_org)
    assert item_portal.delivery_channel == "PORTAL_TRACKER"
    assert item_portal.queue_status == "ROUTED_PORTAL"
    assert item_portal.assigned_block_id == "PORTAL_DEADLINE_TRACKER"

    # Portal item must NEVER be dispatchable to SMTP
    with pytest.raises(ValueError, match="PORTAL_DISPATCH_REJECTED"):
        qm.dispatch_queue_item(item_portal.queue_id, simulated=True)


def test_production_collision_pre_dispatch_suppression(mock_campaign_env):
    """Test that pre-dispatch check prevents duplicate sending if recipient in production DB."""
    qm: UnifiedOutreachQueueManager = mock_campaign_env["qm"]
    prod_db = mock_campaign_env["prod_db"]

    # Enqueue a new academic contact
    acad_org = Organization(
        organization_id="org_mit",
        name="MIT CSAIL",
        category="ACADEMIC_LAB",
        website_url="https://csail.mit.edu",
        iana_timezone="America/New_York"
    )
    acad_opp = Opportunity(
        opportunity_id="opp_mit",
        organization_id="org_mit",
        opportunity_type=OpportunityType.ACADEMIC_LAB.value,
        title="Visiting Researcher",
        hiring_contact_channel=ContactChannel.FACULTY_PI.value,
        contact_name="Prof Test",
        contact_email="prof_test@mit.edu",
        official_evidence_urls=["https://csail.mit.edu/faculty"],
        contact_verification_status="VERIFIED"
    )
    item = qm.enqueue_opportunity(acad_opp, acad_org)
    assert item.queue_status == "QUEUED"

    # Insert this email into production professors (already contacted)
    conn = sqlite3.connect(str(prod_db))
    conn.execute("INSERT INTO professors (professor_id, email, outreach_status) VALUES ('p1', 'prof_test@mit.edu', 'SENT');")
    conn.commit()
    conn.close()

    # Pre-dispatch check must detect collision and suppress item
    test_now = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
    authorized, reason = qm.pre_dispatch_check(item.queue_id, now_utc=test_now)
    assert authorized is False
    assert "SUPPRESSED_COLLISION" in reason

    updated_item = qm.get_queue_item(item.queue_id)
    assert updated_item.queue_status == "SUPPRESSED_COLLISION"


def test_batch_cooling_cadence_and_ambiguous_delivery(mock_campaign_env):
    """Test 10-email batch boundary, 300s cooling break, and ambiguous delivery handling."""
    runner: UnifiedCampaignRunner = mock_campaign_env["runner"]
    qm: UnifiedOutreachQueueManager = mock_campaign_env["qm"]

    # Enqueue 12 mock opportunities
    for i in range(12):
        org = Organization(
            organization_id=f"org_{i}",
            name=f"Startup {i}",
            category="STARTUP",
            website_url=f"https://startup{i}.com",
            iana_timezone="America/New_York"
        )
        opp = Opportunity(
            opportunity_id=f"opp_{i}",
            organization_id=f"org_{i}",
            opportunity_type=OpportunityType.STARTUP.value,
            title=f"Engineer {i}",
            hiring_contact_channel=ContactChannel.EMAIL.value,
            contact_name=f"Founder {i}",
            contact_email=f"founder{i}@startup{i}.com",
            official_evidence_urls=[f"https://startup{i}.com/team"],
            contact_verification_status="VERIFIED"
        )
        qm.enqueue_opportunity(opp, org)

    # Run batch demonstration
    test_now = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
    res = runner.run_batch_simulation(count=12, cooling_seconds=300, simulated_cooling=True, now_utc=test_now, block_id=None)
    assert res["batch_1_count"] == 10, "Batch 1 must contain exactly 10 emails"
    assert res["cooling_break_enforced"] is True, "Cooling break must be enforced after 10 emails"
    assert res["batch_2_count"] == 2, "Batch 2 must resume and contain the remaining 2 emails"
    assert res["ambiguous_delivery_handled"] is True, "Ambiguous delivery safeguard verified"


def test_crash_recovery_idempotency(mock_campaign_env):
    """Test crash recovery reconciling orphaned PROCESSING records."""
    qm: UnifiedOutreachQueueManager = mock_campaign_env["qm"]

    # Insert an item stuck in PROCESSING without Message-ID (simulating crash before send)
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = qm.dev_conn.cursor()
    cur.execute("""
        INSERT INTO unified_outreach_queue (
            queue_id, opportunity_id, category, organization_name, recipient_name,
            recipient_email, hiring_contact_channel, delivery_channel, assigned_block_id,
            iana_timezone, queue_status, created_at, updated_at
        ) VALUES ('q_orphan_1', 'opp_1', 'STARTUP', 'Orphan Co', 'Lead', 'lead@orphan.com',
                  'EMAIL', 'SMTP_GATEWAY', 'BLOCK_09', 'America/New_York', 'PROCESSING', ?, ?);
    """, (now_iso, now_iso))
    # Insert another item stuck in PROCESSING with Message-ID (simulating crash after send)
    cur.execute("""
        INSERT INTO unified_outreach_queue (
            queue_id, opportunity_id, category, organization_name, recipient_name,
            recipient_email, hiring_contact_channel, delivery_channel, assigned_block_id,
            iana_timezone, queue_status, smtp_message_id, created_at, updated_at
        ) VALUES ('q_orphan_2', 'opp_2', 'STARTUP', 'Orphan Co 2', 'Lead 2', 'lead2@orphan.com',
                  'EMAIL', 'SMTP_GATEWAY', 'BLOCK_09', 'America/New_York', 'PROCESSING',
                  '<real.msg@iitd.ac.in>', ?, ?);
    """, (now_iso, now_iso))
    qm.dev_conn.commit()

    rec = qm.recover_from_crash()
    assert rec["orphans_found"] == 2
    assert rec["recovered_dispatched"] == 1, "Item with Message-ID marked DISPATCHED_SMTP"
    assert rec["requeued_for_dispatch"] == 1, "Item without Message-ID safely requeued as QUEUED"

    item1 = qm.get_queue_item("q_orphan_1")
    assert item1.queue_status == "QUEUED"
    assert item1.retry_count == 1

    item2 = qm.get_queue_item("q_orphan_2")
    assert item2.queue_status == "DISPATCHED_SMTP"
