"""
Operational Observability Dashboard Server Launcher.
Runs the FastAPI web server locally on 127.0.0.1:8000 using Uvicorn.
Provides read-only monitoring of:
1. Autonomous Opportunity Discovery
2. Unified Email Outreach & Regional Scheduler
3. Portal Application Automation
4. Replies, Bounces, and Outcome Tracking
5. Master Excel Workbook Parity
"""

import os
import sys
import argparse
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import uvicorn
from config.settings import settings


def main():
    parser = argparse.ArgumentParser(description="Run Opportunity Intelligence Engine Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print("=" * 80)
    print("   OPPORTUNITY INTELLIGENCE ENGINE — OPERATIONS OBSERVABILITY CENTER")
    print(f"   Local URL:    http://{args.host}:{args.port}")
    print(f"   Database:     {settings.SQLITE_DB_PATH}")
    print(f"   Workbook:     {settings.MASTER_WORKBOOK_PATH}")
    print(f"   Active Lock:  data/scheduler.lock (Running Sender PID 5136)")
    print("=" * 80)

    # Launch uvicorn
    uvicorn.run(
        "src.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        access_log=False
    )


if __name__ == "__main__":
    main()
