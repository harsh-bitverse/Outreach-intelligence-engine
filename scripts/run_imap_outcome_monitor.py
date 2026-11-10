"""
Opportunity Intelligence Engine: Supervised IMAP Outcome & Bounce Monitor Runner.

Polls IIT Delhi mailstore (mailstore.iitd.ac.in:993) strictly in read-only mode,
reconciles inbound replies, detects DSN delivery failures, classifies bounces,
and generates non-sending candidate response drafts.

Usage:
  .venv\\Scripts\\python.exe scripts\\run_imap_outcome_monitor.py --once
  .venv\\Scripts\\python.exe scripts\\run_imap_outcome_monitor.py --daemon --interval 300
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from src.mail.bounce_monitor import IMAPBounceMonitor
from src.mail.bounce_recovery import BounceRecoveryService
from src.security.audit_logger import audit_logger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("imap_outcome_monitor")

_keep_running = True


def _handle_signal(signum, frame):
    global _keep_running
    logger.info("Shutdown signal received. Exiting IMAP monitor gracefully...")
    _keep_running = False


def run_single_cycle() -> dict:
    """Executes a single read-only IMAP bounce check and recovery review."""
    monitor = IMAPBounceMonitor()
    recovery_svc = BounceRecoveryService()
    
    logger.info("Connecting to IIT Delhi IMAP mailstore in read-only mode...")
    new_bounces = 0
    try:
        new_bounces = monitor.check_for_new_bounces()
    except Exception as e:
        logger.warning(f"IMAP check error (non-fatal, continuing): {e}")

    recovery_records = recovery_svc.list_recovery_records()

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_dsns_detected": new_bounces,
        "total_recovery_candidates": len(recovery_records),
        "status": "HEALTHY"
    }
    logger.info(f"Cycle completed: {new_bounces} new DSNs detected. {len(recovery_records)} bounce recovery candidates active.")
    return result


def main():
    parser = argparse.ArgumentParser(description="IMAP Outcome & Bounce Monitor")
    parser.add_argument("--once", action="store_true", help="Run single incremental check and exit")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background daemon mode")
    parser.add_argument("--interval", type=int, default=300, help="Interval in seconds between checks (default: 300s)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.daemon:
        logger.info(f"[IMAP MONITOR] Starting daemon (interval: {args.interval}s, PID: {os.getpid()})...")
        while _keep_running:
            try:
                run_single_cycle()
            except Exception as e:
                logger.error(f"Error in monitor cycle: {e}", exc_info=True)
            
            slept = 0
            while _keep_running and slept < args.interval:
                time.sleep(min(1.0, float(args.interval - slept)))
                slept += 1
        logger.info("[IMAP MONITOR] Graceful shutdown complete.")
    else:
        logger.info("[IMAP MONITOR] Executing single incremental check...")
        res = run_single_cycle()
        print("\n=== IMAP MONITOR RESULT ===")
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
