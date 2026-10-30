"""
Session 07: Autonomous Professor Outreach Campaign CLI & Deliverable Demonstration.

Executes the exact loop:
1. Verifies IST time against the 13 regional time blocks and checks professor-local working day eligibility.
2. Directly reads 'Time Blocks & Distribution' sheet in original row sequence.
3. Invokes context-grounded AI authoring (strictly factual, 130–180 words, 4 paragraphs, dual attachments).
4. Dispatches message through IITD SMTP gateway (dry-run protected until Monday activation).
5. Transactionally updates SQLite queue, drafts, and professors tables.
6. Atomically updates the SAME 'Time Blocks & Distribution' worksheet row to SENT with timestamp & Message-ID.
7. Demonstrates automatic block transition and resumption across blocks without per-professor approval.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.scheduler.autonomous_campaign_loop import (
    AutonomousCampaignLoop,
    autonomous_campaign_loop,
    CAMPAIGN_START_DATE
)
from src.scheduler.queue_runner import TIME_BLOCK_WINDOWS


def banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def section(title: str):
    print("\n" + "-" * 80)
    print(f"  {title}")
    print("-" * 80)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Professor Outreach Loop Runner")
    parser.add_argument("--demo", action="store_true", default=True, help="Run complete 3-professor + block transition demonstration")
    parser.add_argument("--block", type=str, default="BLOCK_01", help="Initial block to process (default: BLOCK_01)")
    parser.add_argument("--next-block", type=str, default="BLOCK_02", help="Block to demonstrate automatic resumption (default: BLOCK_02)")
    parser.add_argument("--count", type=int, default=3, help="Number of professors to process in initial block (default: 3)")
    parser.add_argument("--live", action="store_true", default=False, help="Enable live transmission (CAUTION: requires operator activation)")
    args = parser.parse_args()

    banner("SESSION 07: AUTONOMOUS PROFESSOR OUTREACH LOOP DEMONSTRATION")
    print(f"  Applicant: Harshith N (ch1251635@iitd.ac.in) | IIT Delhi")
    print(f"  Target: Summer 2027 Visiting Research Internship")
    print(f"  Authoritative Campaign Start: {CAMPAIGN_START_DATE.strftime('%A, %B %d, %Y')}")
    print(f"  Authoritative Workbook: {settings.MASTER_WORKBOOK_PATH.name}")
    print(f"  Execution Mode: {'LIVE SMTP DISPATCH' if args.live else 'SIMULATED DISPATCH (DRY-RUN SAFEGUARD ACTIVE)'}")
    print(f"  Timestamp: {datetime.now().isoformat()}")

    initialize_database()

    loop = autonomous_campaign_loop

    # 1. Architectural & Safety Checks
    section("1. Architectural Schedule & Working Day Configuration")
    ist_tz = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist_tz)
    print(f"  Current System Time (IST): {now_ist.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    active_block = loop.get_active_block(now_ist)
    if active_block:
        b_id, b_info = active_block
        print(f"  Active IST Block Right Now: {b_id} ({b_info['name']}) | Window: {b_info['start_ist']}-{b_info['end_ist']} IST")
    else:
        print("  Active IST Block Right Now: None currently active (System waiting for next block window)")

    # Test working day eligibility on Monday Nov 2, 2026 for Block 01 (New Zealand)
    monday_ist = datetime(2026, 11, 2, 1, 30, tzinfo=ist_tz)
    is_workday, details = loop.is_local_working_day("Pacific/Auckland", monday_ist)
    print(f"\n  Monday Activation Window Check (01:30 IST on Nov 2, 2026):")
    print(f"    - Target Region: New Zealand (Pacific/Auckland)")
    print(f"    - Local Calculation: {details}")
    print(f"    - Weekday Verified: {is_workday} (No Tuesday–Thursday restriction applied)")

    # 2. Document & Attachment Check
    section("2. Dual PDF Attachment Verification")
    cv_p = settings.CV_ATTACHMENT_PATH
    ri_p = settings.RESEARCH_INTERESTS_PATH
    print(f"  Attachment 1: {settings.CV_ATTACHMENT_FILENAME}")
    print(f"    Path: {cv_p} | Exists: {cv_p.exists()} | Bytes: {cv_p.stat().st_size:,}")
    print(f"  Attachment 2: {settings.RESEARCH_INTERESTS_FILENAME}")
    print(f"    Path: {ri_p} | Exists: {ri_p.exists()} | Bytes: {ri_p.stat().st_size:,}")
    assert cv_p.exists() and ri_p.exists(), "Fatal: Authoritative attachments missing!"

    # 3. Demonstration Sequence
    section(f"3. Executing Autonomous Loop Sequence: {args.count} Professors in {args.block}")
    dry_run = not args.live

    res = loop.run_simulation_sequence(
        block_id=args.block,
        next_block_id=args.next_block,
        count=args.count,
        dry_run=dry_run
    )

    print(f"\n  Successfully Processed {len(res['professors_processed'])} Professors in {args.block}:")
    for idx, item in enumerate(res["professors_processed"], start=1):
        print(f"\n  [{idx}/{args.count}] {item['professor_name']} ({item['university']})")
        print(f"       Email            : {item['email']}")
        print(f"       Block            : {item['block_id']}")
        print(f"       Subject          : {item['subject']}")
        print(f"       Message-ID       : {item['message_id']}")
        print(f"       Dispatch Status  : {item['status']}")
        print(f"       Sent Timestamp   : {item['sent_at']}")
        print(f"       Excel Synchronized: {item['excel_updated']} (Sheet: 'Time Blocks & Distribution')")

    # 4. Block Transition & Resumption Demonstration
    section(f"4. Automatic Block Change & Resumption: {args.block} -> {args.next_block}")
    if res["block_transition_demonstrated"] and res["resumed_professor"]:
        rp = res["resumed_professor"]
        print(f"  [+] Block transition triggered automatically upon completion of {args.block}.")
        print(f"  [+] Transitioned to: {args.next_block} ({TIME_BLOCK_WINDOWS.get(args.next_block, {}).get('name', 'Unknown')})")
        print(f"  [+] Resumed Processing Next Professor in {args.next_block}:")
        print(f"       Professor Name   : {rp['professor_name']}")
        print(f"       University       : {rp['university']}")
        print(f"       Email            : {rp['email']}")
        print(f"       Subject          : {rp['subject']}")
        print(f"       Message-ID       : {rp['message_id']}")
        print(f"       Dispatch Status  : {rp['status']}")
        print(f"       Excel Synchronized: {rp['excel_updated']}")
        print("\n  [+] Unattended cross-block continuation verified: ZERO per-professor approvals required.")
    else:
        print("  [-] Block transition could not find unsent candidate in next block.")

    # 5. Database & Excel Sent State Verification
    section("5. Transactional Integrity Audit (SQLite & Excel)")
    with get_db() as conn:
        sent_in_db = conn.execute(
            "SELECT COUNT(*) FROM outreach_queue WHERE status IN ('SENT', 'SIMULATED_SENT');"
        ).fetchone()[0]
        drafts_in_db = conn.execute(
            "SELECT COUNT(*) FROM outreach_drafts WHERE status IN ('SENT', 'SIMULATED_SENT', 'DISPATCHED');"
        ).fetchone()[0]
        print(f"  SQLite Queue Sent Records  : {sent_in_db}")
        print(f"  SQLite Draft Records       : {drafts_in_db}")

    print("\n  [OK] Session 07 Autonomous Outreach Campaign Demonstration Completed Successfully.")


if __name__ == "__main__":
    main()
