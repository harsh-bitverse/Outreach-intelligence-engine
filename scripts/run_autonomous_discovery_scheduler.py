"""
Opportunity Intelligence Engine: Autonomous Discovery Scheduler Runner.
Supervised 24/7 Dual-Stream Discovery Worker.

Usage:
  .venv\\Scripts\\python.exe scripts\\run_autonomous_discovery_scheduler.py --once
  .venv\\Scripts\\python.exe scripts\\run_autonomous_discovery_scheduler.py --steps 5 --interval 2
  .venv\\Scripts\\python.exe scripts\\run_autonomous_discovery_scheduler.py --daemon --interval 30
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from src.opportunities.autonomous_discovery_scheduler import AutonomousDiscoveryScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("autonomous_discovery_runner")

_keep_running = True


def _handle_signal(signum, frame):
    global _keep_running
    logger.info("Shutdown signal received. Finishing active screening step gracefully...")
    _keep_running = False


def main():
    parser = argparse.ArgumentParser(description="Autonomous Discovery Scheduler Runner")
    parser.add_argument("--once", action="store_true", help="Execute single university/startup screening step and exit")
    parser.add_argument("--steps", type=int, default=1, help="Number of screening steps to execute (default: 1)")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background daemon mode")
    parser.add_argument("--interval", type=float, default=15.0, help="Interval in seconds between screening steps (default: 15s)")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    scheduler = AutonomousDiscoveryScheduler()

    if args.daemon:
        logger.info(f"[AUTONOMOUS DISCOVERY] Starting continuous 24/7 daemon (interval: {args.interval}s, PID: {os.getpid()})...")
        step_num = 0
        while _keep_running:
            step_num += 1
            try:
                res = scheduler.run_continuous_supervision_step()
                uni_res = res.get("stream_a_university", {})
                logger.info(
                    f"[STEP {step_num}] Screened {uni_res.get('name')} (Rank {uni_res.get('rank')}, Phase {uni_res.get('phase')}): "
                    f"+{uni_res.get('shortlisted')} shortlisted, {uni_res.get('duplicates')} duplicates ({res.get('duration_seconds')}s)"
                )
            except Exception as e:
                logger.error(f"[STEP {step_num}] Error in screening cycle: {e}", exc_info=True)
            
            # Sleep in small increments for responsive shutdown
            slept = 0.0
            while _keep_running and slept < args.interval:
                time.sleep(min(1.0, args.interval - slept))
                slept += 1.0
        logger.info("[AUTONOMOUS DISCOVERY] Graceful daemon shutdown complete.")
    elif args.once or args.steps == 1:
        logger.info("[AUTONOMOUS DISCOVERY] Executing single screening step...")
        res = scheduler.run_continuous_supervision_step()
        print("\n=== SCREENING RESULT ===")
        print(json.dumps(res, indent=2))
    else:
        logger.info(f"[AUTONOMOUS DISCOVERY] Executing {args.steps} screening steps (interval: {args.interval}s)...")
        for i in range(1, args.steps + 1):
            if not _keep_running:
                break
            res = scheduler.run_continuous_supervision_step()
            uni_res = res.get("stream_a_university", {})
            print(f"\n--- STEP {i}/{args.steps}: {uni_res.get('name')} (Rank {uni_res.get('rank')}) ---")
            print(json.dumps(res, indent=2))
            if i < args.steps:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
