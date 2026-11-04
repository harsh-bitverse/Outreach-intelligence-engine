"""
Script to execute full startup discovery sweep (100+ organizations) into isolated development environment.
Guarantees zero writes to production SQLite (data/outreach_engine.db) or master workbook.
"""

import os
import sys
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

# Ensure UTF-8 console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config.settings import settings
from src.opportunities.schema import apply_opportunity_migrations
from src.opportunities.repository import OpportunityRepository
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.opportunities.discovery_worker import OpportunityDiscoveryWorker
from src.opportunities.scheduling import OpportunityScheduler
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.opportunities.models import OpportunityType, CompensationType, ContactChannel, PriorityTier


def main():
    print("=" * 80)
    print("OPPORTUNITY INTELLIGENCE ENGINE: SESSION 02 FULL STARTUP DISCOVERY SWEEP")
    print("=" * 80)

    # 1. Setup isolated development paths
    dev_dir = settings.DATA_DIR / "development"
    dev_dir.mkdir(parents=True, exist_ok=True)

    dev_db_path = dev_dir / "opportunity_dev.db"
    dev_wb_path = dev_dir / "Summer_2027_Opportunity_Workbook.xlsx"
    dev_lock_path = dev_dir / "discovery_worker.lock"

    print(f"[*] Isolated Development Database : {dev_db_path}")
    print(f"[*] Isolated Development Workbook : {dev_wb_path}")
    print(f"[*] Isolated Lock Path            : {dev_lock_path}")

    # Copy master workbook to dev workbook if not exists or initialize
    if not dev_wb_path.exists():
        print("[*] Creating isolated copy of master workbook...")
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, dev_wb_path)

    # Initialize dev SQLite DB
    conn = sqlite3.connect(dev_db_path)
    # Apply base tables and opportunity migrations
    apply_opportunity_migrations(conn)
    repo = OpportunityRepository(conn)

    sync = OpportunityWorkbookSync(workbook_path=dev_wb_path)
    sync.initialize_opportunity_sheets()

    # 2. Run Startup Discovery Sweep
    worker = OpportunityDiscoveryWorker(
        repo=repo,
        workbook_sync=sync,
        lock_path=dev_lock_path
    )

    print("\n[*] Launching full global startup discovery sweep (limit: 150)...")
    sweep_res = worker.run_adapter_sweep(adapter_name="StartupAdapter", limit=150)
    print(f"[+] Discovery sweep completed in {sweep_res['duration_seconds']}s")
    print(f"    - Opportunities Found     : {sweep_res['opportunities_found']}")
    print(f"    - Opportunities Qualified : {sweep_res['opportunities_qualified']}")

    # 3. Analyze discovered data in repo
    all_startups = repo.list_opportunities(category=OpportunityType.STARTUP.value, limit=200)
    total_count = len(all_startups)
    orgs_by_id = {opp.organization_id: repo.get_organization(opp.organization_id) for opp in all_startups}

    print(f"\n[+] Total Startups Discovered in DB: {total_count}")

    # Breakdown metrics
    confirmed_openings = [opp for opp in all_startups if opp.is_confirmed_opening]
    prospective_targets = [opp for opp in all_startups if not opp.is_confirmed_opening]
    paid_opps = [opp for opp in all_startups if opp.compensation_type in (CompensationType.PAID.value, CompensationType.STIPEND.value)]
    unknown_comp_opps = [opp for opp in all_startups if opp.compensation_type == CompensationType.UNKNOWN.value]

    high_priority = [opp for opp in all_startups if opp.priority_tier == PriorityTier.HIGH_PRIORITY.value]
    promising = [opp for opp in all_startups if opp.priority_tier == PriorityTier.PROMISING.value]
    needs_verif = [opp for opp in all_startups if opp.priority_tier == PriorityTier.NEEDS_VERIFICATION.value]

    email_contacts = [opp for opp in all_startups if opp.hiring_contact_channel == ContactChannel.EMAIL.value and opp.contact_email]
    portal_contacts = [opp for opp in all_startups if opp.hiring_contact_channel == ContactChannel.APPLICATION_PORTAL.value or not opp.contact_email]

    print("\n" + "-" * 40)
    print("DETAILED BREAKDOWN METRICS:")
    print("-" * 40)
    print(f"Total Unique Organizations Discovered : {len(orgs_by_id)}")
    print(f"Total Opportunities Discovered        : {total_count}")
    print(f"  • Confirmed Internship Openings     : {len(confirmed_openings)}")
    print(f"  • Prospective Cold-Outreach Targets : {len(prospective_targets)}")
    print(f"  • Explicitly Paid / Stipend         : {len(paid_opps)}")
    print(f"  • Unknown Compensation (Preserved)  : {len(unknown_comp_opps)}")
    print(f"\nPriority Tiers:")
    print(f"  • HIGH_PRIORITY (Score >= 80.0)     : {len(high_priority)}")
    print(f"  • PROMISING     (Score 65.0 - 79.9) : {len(promising)}")
    print(f"  • NEEDS_VERIF   (Score 50.0 - 64.9) : {len(needs_verif)}")
    print(f"\nRouting Channels:")
    print(f"  • Direct Founder / Email Outreach   : {len(email_contacts)}")
    print(f"  • Official Careers Portal / ATS     : {len(portal_contacts)}")

    # 4. Generate Unified Outreach Schedule
    schedule_records = OpportunityScheduler.generate_unified_schedule(all_startups, orgs_by_id)
    sched_synced = sync.sync_unified_schedule(schedule_records)
    print(f"\n[+] Synchronized {sched_synced} records to 'Unified Outreach Schedule' sheet")

    # 5. Top 20 Ranked Startup Opportunities
    print("\n" + "=" * 80)
    print("TOP 20 RANKED STARTUP OPPORTUNITIES (SUMMER 2027):")
    print("=" * 80)
    for rank, opp in enumerate(all_startups[:20], start=1):
        org = orgs_by_id.get(opp.organization_id)
        org_name = org.name if org else opp.organization_id
        print(f"#{rank:02d} | Score: {opp.qualification_score:4.1f} [{opp.priority_tier}] | {org_name}")
        print(f"     Title: {opp.title}")
        print(f"     Location: {opp.location} ({opp.iana_timezone}) | Term: {opp.target_term}")
        print(f"     Compensation: {opp.compensation_details or opp.compensation_type}")
        print(f"     Matched Project: {opp.matched_project_id}")
        print(f"     Contribution: {opp.potential_contribution[:90]}..." if opp.potential_contribution else "")
        print(f"     Evidence URL: {opp.official_evidence_urls[0] if opp.official_evidence_urls else 'N/A'}")
        print(f"     Route: {opp.hiring_contact_channel} -> {opp.contact_email or opp.application_url}")
        print("-" * 80)

    # 6. Sample Domain-Adaptive Pitch
    sample_opp = all_startups[0]
    sample_org = orgs_by_id.get(sample_opp.organization_id)
    if sample_org:
        pitch = DomainAdaptivePersonalizer.generate_startup_pitch(sample_opp, sample_org)
        print("\n" + "=" * 80)
        print(f"SAMPLE FOUNDER ENGINEERING PITCH ({sample_org.name}):")
        print("=" * 80)
        print(f"Subject: {pitch['pitch_subject']}")
        print(f"Recipient: {pitch['recipient_name']} <{pitch['recipient_email']}>")
        print("-" * 80)
        print(pitch["pitch_body"])
        print("=" * 80)

    conn.close()
    print("\n[✔] FULL SWEEP EXECUTED SUCCESSFULLY IN ISOLATION.")


if __name__ == "__main__":
    main()
