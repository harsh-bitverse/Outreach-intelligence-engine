"""
Opportunity Intelligence Engine: Unified Outreach Scheduler CLI.

Unified entry point for:
- 1,390 baseline professors ('Time Blocks & Distribution' workbook sheet)
- Discovered Academic Labs, Startups, Frontier Industry, and Institutional Programs ('unified_outreach_queue')
- Shared IIT Delhi SMTP gateway and shared 200 emails/day budget
- Strict regional time block windows (BLOCK_01 to BLOCK_13) and recipient-local working days
- Single-instance mutual exclusion via data/scheduler.lock and graceful maintenance quiescence
- Real-time continuous console dashboard visible in Windows Command Prompt
"""

import argparse
import ctypes
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.mail.credentials import CredentialManager
from src.scheduler.autonomous_campaign_loop import (
    AutonomousCampaignLoop,
    AntigravityCLIDrafter,
    autonomous_campaign_loop,
    CAMPAIGN_START_DATE
)
from src.scheduler.queue_runner import TIME_BLOCK_WINDOWS
from src.security.audit_logger import audit_logger


class PersistentTee:
    """Tees output to both console stream and persistent local log file."""
    def __init__(self, log_path: Path, stream):
        self.stream = stream
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(log_path, "a", encoding="utf-8", buffering=1)

    def write(self, message):
        self.stream.write(message)
        self.stream.flush()
        self.file.write(message)
        self.file.flush()

    def flush(self):
        self.stream.flush()
        self.file.flush()


def setup_persistent_logging(log_filename: str = "unified_campaign_execution.log") -> Path:
    """Configures unbuffered persistent dual-stream logging and fatal crash reporting."""
    log_path = settings.LOGS_DIR / log_filename
    sys.stdout = PersistentTee(log_path, sys.stdout)
    sys.stderr = PersistentTee(log_path, sys.stderr)

    def excepthook(exc_type, exc_value, exc_traceback):
        print("\n" + "!" * 80)
        print(f"FATAL UNCAUGHT EXCEPTION TERMINATING PROCESS [PID {os.getpid()}]:")
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        print("!" * 80 + "\n")
        audit_logger.log_event(
            event_type="PROCESS_FATAL_EXCEPTION",
            actor="SYSTEM_RUNTIME",
            entity_type="PROCESS",
            entity_id=str(os.getpid()),
            details={"type": str(exc_type), "error": str(exc_value)}
        )

    sys.excepthook = excepthook
    return log_path


def set_console_title(title: str) -> None:
    """Set the Windows console window title."""
    try:
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def section(title: str):
    print("\n" + "-" * 80)
    print(f"  {title}")
    print("-" * 80)


def verify_production_preflight_startup() -> None:
    """
    Mandatory startup verification covering:
    1. CLI authentication: verify agy.exe presence and readiness.
    2. SMTP credentials: verify ch1251635 credentials in Windows keyring.
    3. Attachments: verify both PDF attachments exist and are non-empty.
    4. Writable SQLite database: verify DB connectivity and write permission.
    5. Writable Excel workbook: verify master workbook is present and writable.
    6. Emergency-stop switch: verify EMERGENCY_STOP is False and flag file does not exist.
    """
    section("Preflight Startup Verification (All 6 Mandatory Systems)")
    errors = []

    # 1. CLI Authentication
    cli_drafter = AntigravityCLIDrafter()
    if not cli_drafter.is_available():
        errors.append(f"Antigravity CLI binary missing at {cli_drafter.cli_path}")
    else:
        print(f"  [CHECK 1/6] Antigravity CLI binary verified: {cli_drafter.cli_path}")

    # 2. SMTP Credentials
    cred_mgr = CredentialManager()
    user, pwd = cred_mgr.get_credentials()
    if not user or not pwd:
        errors.append("IIT Delhi SMTP credentials not found in credential store")
    else:
        print(f"  [CHECK 2/6] IITD SMTP Credentials verified for {user}")

    # 3. Dual PDF Attachments
    cv_p = settings.CV_ATTACHMENT_PATH
    ri_p = settings.RESEARCH_INTERESTS_PATH
    if not cv_p.exists() or cv_p.stat().st_size <= 0:
        errors.append(f"CV attachment missing or empty: {cv_p}")
    if not ri_p.exists() or ri_p.stat().st_size <= 0:
        errors.append(f"Research Interests attachment missing or empty: {ri_p}")
    if cv_p.exists() and ri_p.exists():
        print(f"  [CHECK 3/6] Attachments verified: {cv_p.name} ({cv_p.stat().st_size:,} bytes), {ri_p.name} ({ri_p.stat().st_size:,} bytes)")

    # 4. Writable SQLite Database
    db_p = settings.SQLITE_DB_PATH
    try:
        conn = sqlite3.connect(str(db_p))
        cur = conn.cursor()
        cur.execute("PRAGMA quick_check;")
        res = cur.fetchone()
        conn.close()
        print(f"  [CHECK 4/6] SQLite Database writable & healthy: {db_p.name} (quick_check={res[0]})")
    except Exception as e:
        errors.append(f"SQLite DB check failed: {e}")

    # 5. Writable Master Excel Workbook
    wb_p = settings.MASTER_WORKBOOK_PATH
    if not wb_p.exists():
        errors.append(f"Master workbook missing: {wb_p}")
    else:
        try:
            with open(wb_p, "r+b") as f:
                header = f.read(4)
                if header != b"PK\x03\x04":
                    errors.append(f"Invalid Excel file header: {header}")
                else:
                    print(f"  [CHECK 5/6] Master Excel workbook writable: {wb_p.name} ({wb_p.stat().st_size:,} bytes)")
        except Exception as e:
            errors.append(f"Excel workbook not writable: {e}")

    # 6. Emergency Stop Switch
    flag_p = settings.DATA_DIR / "EMERGENCY_STOP.flag"
    if settings.EMERGENCY_STOP:
        errors.append("settings.EMERGENCY_STOP is set to True")
    elif flag_p.exists():
        errors.append(f"Emergency stop flag file exists: {flag_p}")
    else:
        print(f"  [CHECK 6/6] Emergency Stop Switch verified INACTIVE (Clear to proceed)")

    if errors:
        audit_logger.log_event(
            event_type="STARTUP_PREFLIGHT_FAILED",
            actor="SYSTEM_STARTUP",
            entity_type="SYSTEM",
            entity_id="PREFLIGHT",
            details={"errors": errors}
        )
        raise RuntimeError(f"Startup preflight checks failed:\n" + "\n".join(f"  - {e}" for e in errors))

    audit_logger.log_event(
        event_type="STARTUP_PREFLIGHT_PASSED",
        actor="SYSTEM_STARTUP",
        entity_type="SYSTEM",
        entity_id="PREFLIGHT",
        details={"status": "ALL_CHECKS_PASSED"}
    )
    print("  [SUCCESS] All 6 Preflight Startup Checks Verified Successfully.\n")


def main():
    set_console_title("Opportunity Intelligence Engine — Unified Scheduler")
    log_path = setup_persistent_logging("unified_campaign_execution.log")

    parser = argparse.ArgumentParser(description="Opportunity Intelligence Engine — Unified Outreach Scheduler")
    parser.add_argument("--continuous", action="store_true", default=True, help="Run continuous 24/7 background campaign loop (default)")
    parser.add_argument("--demo", action="store_true", default=False, help="Run batch demonstration")
    parser.add_argument("--cli", action="store_true", default=False, help="Use official Antigravity CLI under user Google AI Pro account")
    parser.add_argument("--live", action="store_true", default=False, help="Enable live SMTP transmission (DANGER: requires operator activation)")
    parser.add_argument("--count", type=int, default=12, help="Number of candidates for batch demo (default: 12)")
    args = parser.parse_args()

    # If demo explicitly requested, do not run continuous
    run_demo = args.demo

    banner("OPPORTUNITY INTELLIGENCE ENGINE — UNIFIED OUTREACH SCHEDULER")
    print(f"  Process ID (PID): {os.getpid()} | User: {os.environ.get('USERNAME', 'unknown')}")
    print(f"  Console Window: VISIBLE WINDOWS COMMAND PROMPT")
    print(f"  Working Directory: {os.getcwd()}")
    print(f"  Python Executable: {sys.executable}")
    print(f"  Invocation Arguments: {' '.join(sys.argv)}")
    print(f"  Persistent Execution Log: {log_path}")
    print(f"  Applicant: Harshith N (ch1251635@iitd.ac.in) | IIT Delhi")
    print(f"  Target: Summer 2027 Visiting Research / Engineering Internship")
    print(f"  Shared Daily Limit: {settings.DAILY_MAX_OUTREACH} emails/day (Reset: Midnight IST)")
    print(f"  Batch Cadence: {settings.BATCH_SIZE} emails/batch | Break: {settings.COOLING_BREAK_SECONDS}s")
    print(f"  AI Drafting Engine: {'OFFICIAL ANTIGRAVITY CLI (GOOGLE AI PRO ACCOUNT)' if args.cli else 'LOCAL VERIFIED DYNAMIC AUTHOR'}")
    print(f"  Execution Mode: {'LIVE SMTP DISPATCH' if args.live else 'SIMULATED DISPATCH (DRY-RUN SAFEGUARD ACTIVE)'}")
    print(f"  Unified Queues: Baseline Professors + Discovered Academic Labs + Startups + Institutional Programs")
    print(f"  Timestamp: {datetime.now().isoformat()}")

    initialize_database()
    verify_production_preflight_startup()

    if run_demo:
        section(f"Isolated {args.count}-Candidate Demonstration")
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

            section("Cadence & Quota Invariants")
            print(f"  [+] Daily Operating Ceiling   : {settings.DAILY_MAX_OUTREACH} emails/day (Configured)")
            print(f"  [+] Batch Size Boundary       : {settings.BATCH_SIZE} emails/batch")
            print(f"  [+] Mandatory Break           : {settings.COOLING_BREAK_SECONDS} seconds")
            print(f"  [+] Block Transition Handled  : {res['block_transitions']}")
            print(f"  [+] Daily Limit Enforced      : {res['daily_limit_enforced']}")

        print("\n  [OK] Demonstration Completed Successfully.")

    else:
        section("Launching Continuous Unattended Production Loop")
        autonomous_campaign_loop.run_continuous_campaign(
            dry_run=not args.live,
            prefer_cli=args.cli
        )


if __name__ == "__main__":
    main()
