"""
Session 08: Autonomous Outreach Configuration & Monday Launch Readiness CLI.

Configured with:
- Maximum daily sending limit: 200 emails across all 13 blocks combined.
- Batch size: 10 individually personalized emails.
- Break between batches: 60 seconds.
- Daily counter reset: Midnight IST.
- Sending hours: Strictly within active IST blocks and professor-local working days.
- Single-instance lock and emergency killswitch.
"""

import argparse
import shutil
import sys
import tempfile
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
    parser = argparse.ArgumentParser(description="Session 08 Autonomous Outreach Campaign Runner")
    parser.add_argument("--demo", action="store_true", default=False, help="Run 12-professor batch & cadence validation demonstration")
    parser.add_argument("--continuous", action="store_true", default=False, help="Run continuous 24/7 background campaign loop")
    parser.add_argument("--cli", action="store_true", default=False, help="Use official Antigravity CLI under user Google AI Pro account")
    parser.add_argument("--live", action="store_true", default=False, help="Enable live SMTP transmission (DANGER: requires operator activation)")
    parser.add_argument("--count", type=int, default=12, help="Number of professors for batch demo (default: 12)")
    args = parser.parse_args()

    # Default to demo mode if neither continuous nor demo is specified
    run_demo = args.demo or not args.continuous

    banner("SESSION 08: AUTONOMOUS PROFESSOR OUTREACH LAUNCH READINESS")
    print(f"  Applicant: Harshith N (ch1251635@iitd.ac.in) | IIT Delhi")
    print(f"  Target: Summer 2027 Visiting Research Internship")
    print(f"  Campaign Start: {CAMPAIGN_START_DATE.strftime('%A, %B %d, %Y')}")
    print(f"  Operating Ceiling: {settings.DAILY_MAX_OUTREACH} emails/day (Reset: Midnight IST)")
    print(f"  Cadence: {settings.BATCH_SIZE} emails/batch | Break: {settings.COOLING_BREAK_SECONDS}s")
    print(f"  AI Drafting Engine: {'OFFICIAL ANTIGRAVITY CLI (GOOGLE AI PRO ACCOUNT)' if args.cli else 'LOCAL VERIFIED DYNAMIC AUTHOR'}")
    print(f"  Execution Mode: {'LIVE SMTP DISPATCH' if args.live else 'SIMULATED DISPATCH (DRY-RUN SAFEGUARD ACTIVE)'}")
    print(f"  Timestamp: {datetime.now().isoformat()}")

    initialize_database()

    ist_tz = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist_tz)

    # 1. Architectural & Safety Checks
    section("1. Architectural Schedule & Working Day Configuration")
    print(f"  Current System Time (IST): {now_ist.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    active_block = autonomous_campaign_loop.get_active_block(now_ist)
    if active_block:
        b_id, b_info = active_block
        print(f"  Active IST Block Right Now: {b_id} ({b_info['name']}) | Window: {b_info['start_ist']}–{b_info['end_ist']} IST")
    else:
        print("  Active IST Block Right Now: None currently active (System waiting for next block window)")

    # Verify Monday Nov 2, 2026 activation window for Block 01 (New Zealand)
    monday_ist = datetime(2026, 11, 2, 1, 30, tzinfo=ist_tz)
    is_workday, details = autonomous_campaign_loop.is_local_working_day("Pacific/Auckland", monday_ist)
    print(f"\n  Monday Launch Window Verification (01:30 IST on Nov 2, 2026):")
    print(f"    - Target Region: New Zealand (Pacific/Auckland)")
    print(f"    - Working Day Status: {details}")
    print(f"    - Weekday Verified: {is_workday} (Mon–Fri eligible, zero Tuesday–Thursday restrictions)")

    # 2. Document & Attachment Check
    section("2. Dual PDF Attachment Verification")
    cv_p = settings.CV_ATTACHMENT_PATH
    ri_p = settings.RESEARCH_INTERESTS_PATH
    print(f"  Attachment 1: {settings.CV_ATTACHMENT_FILENAME}")
    print(f"    Path: {cv_p} | Exists: {cv_p.exists()} | Bytes: {cv_p.stat().st_size:,}")
    print(f"  Attachment 2: {settings.RESEARCH_INTERESTS_FILENAME}")
    print(f"    Path: {ri_p} | Exists: {ri_p.exists()} | Bytes: {ri_p.stat().st_size:,}")
    assert cv_p.exists() and ri_p.exists(), "Fatal: Authoritative attachments missing!"

    # 3. Demonstration or Continuous Execution
    if run_demo:
        section(f"3. Isolated 12-Professor Cadence & Batch Demonstration")
        print("  Creating isolated simulation environment for testing...")
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_wb = Path(tmpdir) / "demo_workbook.xlsx"
            shutil.copy2(settings.MASTER_WORKBOOK_PATH, temp_wb)

            sync = DynamicWorkbookSync(workbook_path=temp_wb)
            demo_loop = AutonomousCampaignLoop(
                workbook_path=temp_wb,
                sync=sync,
                cli_drafter=autonomous_campaign_loop.cli_drafter if args.cli else None
            )

            print(f"  Executing {args.count}-candidate sequence across blocks...")
            res = demo_loop.run_batch_demonstration(
                count=args.count,
                dry_run=not args.live,
                prefer_cli=args.cli,
                simulated_cooling=True
            )

            print(f"\n  Batch 1 ({res['batch_1_count']} emails):")
            for item in res["professors_processed"][:10]:
                print(f"    [{item['sequence_in_run']}/12] Batch 1 | {item['professor_name']} ({item['university']})")
                print(f"         Block: {item['block_id']} | To: {item['email']}")
                print(f"         Subject: {item['subject']}")
                print(f"         Status: {item['status']} | MsgID: {item['message_id']}")

            if res["cooling_break_enforced"]:
                print(f"\n  >>> [CADENCE MANDATE: Mandatory {res['cooling_break_duration']}-second cooling break enforced after 10 emails] <<<")

            print(f"\n  Batch 2 ({res['batch_2_count']} emails - Resumption Verified):")
            for item in res["professors_processed"][10:]:
                print(f"    [{item['sequence_in_run']}/12] Batch 2 | {item['professor_name']} ({item['university']})")
                print(f"         Block: {item['block_id']} | To: {item['email']}")
                print(f"         Subject: {item['subject']}")
                print(f"         Status: {item['status']} | MsgID: {item['message_id']}")

            section("4. Cadence & Quota Invariants")
            print(f"  [+] Daily Operating Ceiling   : {settings.DAILY_MAX_OUTREACH} emails/day (Configured)")
            print(f"  [+] Batch Size Boundary       : {settings.BATCH_SIZE} emails/batch")
            print(f"  [+] Mandatory Break           : {settings.COOLING_BREAK_SECONDS} seconds")
            print(f"  [+] Block Transition Handled  : {res['block_transitions']}")
            print(f"  [+] Daily Limit Enforced      : {res['daily_limit_enforced']}")
            print(f"  [+] Isolated State Cleaned Up : Temporary simulation state automatically discarded.")

        print("\n  [OK] Session 08 12-Professor Cadence Demonstration Completed Successfully.")

    elif args.continuous:
        section("3. Launching Continuous Unattended Production Loop")
        autonomous_campaign_loop.run_continuous_campaign(
            dry_run=not args.live,
            prefer_cli=args.cli
        )


if __name__ == "__main__":
    main()
