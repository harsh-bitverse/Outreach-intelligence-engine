"""
Outreach Intelligence Engine: Runner for Autonomous Recurring Opportunity Discovery Worker.

Usage:
  .venv\\Scripts\\python.exe scripts\\run_recurring_discovery_worker.py --once
  .venv\\Scripts\\python.exe scripts\\run_recurring_discovery_worker.py --daemon --interval 3600
"""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from src.opportunities.recurring_worker import RecurringDiscoveryWorker


def main():
    parser = argparse.ArgumentParser(description="Autonomous Recurring Opportunity Discovery Worker")
    parser.add_argument("--once", action="store_true", help="Execute a single discovery sweep and exit")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in the background")
    parser.add_argument("--interval", type=int, default=3600, help="Interval in seconds between discovery cycles")
    parser.add_argument("--db-path", type=str, default="data/development/opportunity_dev.db", help="Path to development SQLite")
    parser.add_argument("--workbook-path", type=str, default="data/development/Summer_2027_Opportunity_Workbook.xlsx", help="Path to development workbook")

    args = parser.parse_args()

    worker = RecurringDiscoveryWorker(
        db_path=args.db_path,
        workbook_path=args.workbook_path,
        interval_seconds=args.interval
    )

    if args.daemon:
        print(f"[RECURRING_WORKER] Starting daemon mode (interval: {args.interval}s)...")
        worker.run_daemon()
    else:
        print("[RECURRING_WORKER] Executing single discovery sweep...")
        result = worker.execute_discovery_cycle()
        print("\n=== SWEEP RESULT ===")
        import json
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
