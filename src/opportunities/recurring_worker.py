"""
Opportunity Intelligence Engine: Autonomous Recurring Discovery Worker.
Provides scheduled, non-interactive discovery sweeps across public ATS job boards and catalog sources.
Features:
- Durable checkpoints with restart recovery
- Production priority gating (defers when production outreach is active)
- Strict deduplication, technical fit ranking, and evidence-grounded audit
- Atomic multi-sheet workbook synchronization
- Persistent file logging to logs/recurring_discovery_worker.log
"""

import os
import sys
import time
import signal
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    DiscoveryCheckpoint,
    DiscoveryRun,
    OpportunityClassification,
    CompensationType
)
from src.opportunities.repository import OpportunityRepository
from src.opportunities.adapters.base import BaseOpportunityAdapter
from src.opportunities.adapters.startup import StartupAdapter
from src.opportunities.adapters.live_ats_discovery import LiveATSDiscoveryAdapter
from src.opportunities.adapters.frontier_industry import FrontierIndustryAdapter
from src.opportunities.adapters.institutional_program import InstitutionalProgramAdapter
from src.opportunities.adapters.academic_lab import AcademicLabAdapter
from src.opportunities.audit import audit_opportunity
from src.opportunities.ranking import OpportunityRanker
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.opportunities.scheduling import OpportunityScheduler
from src.security.audit_logger import audit_logger

# Ensure logs directory exists
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "recurring_discovery_worker.log"

logger = logging.getLogger("recurring_discovery_worker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(sh)


class RecurringDiscoveryWorker:
    """
    Independent autonomous discovery worker with quota preservation,
    durable checkpoints, and evidence-based qualification.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        workbook_path: Optional[str] = None,
        interval_seconds: int = 3600
    ):
        self.db_path = db_path or str(settings.DATA_DIR / "development" / "opportunity_dev.db")
        self.workbook_path = workbook_path or str(settings.DATA_DIR / "development" / "Summer_2027_Opportunity_Workbook.xlsx")
        self.interval_seconds = interval_seconds
        self.running = False

        self.adapters: List[BaseOpportunityAdapter] = [
            LiveATSDiscoveryAdapter(),
            StartupAdapter(),
            FrontierIndustryAdapter(),
            InstitutionalProgramAdapter(),
            AcademicLabAdapter()
        ]

    def _is_production_outreach_busy(self) -> bool:
        """
        Non-invasive check to ensure development discovery never contends with live professor outreach.
        Checks if the production database has an active sending lock or recent outbound dispatch.
        """
        prod_db = settings.DATA_DIR / "outreach_engine.db"
        if not prod_db.exists():
            return False

        try:
            # Read-only check with short timeout
            conn = sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            # Check for cooldown or active sending within last 3 minutes
            cur.execute("""
                SELECT 1 FROM outbound_delivery_tracker 
                WHERE dispatched_at >= datetime('now', '-3 minutes') 
                LIMIT 1;
            """)
            busy = bool(cur.fetchone())
            conn.close()
            return busy
        except Exception:
            return False

    def execute_discovery_cycle(self) -> Dict[str, Any]:
        """
        Executes one full discovery sweep across all registered adapters.
        """
        start_time = datetime.now(timezone.utc)
        logger.info(f"=== Starting Discovery Sweep at {start_time.isoformat()} ===")

        # 1. Production Priority Gate
        if self._is_production_outreach_busy():
            logger.info("Production outreach is currently active. Deferring discovery sweep to prioritize production.")
            return {
                "status": "DEFERRED_PRODUCTION_PRIORITY",
                "timestamp": start_time.isoformat(),
                "reason": "Production outreach active in last 3 minutes"
            }

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        repo = OpportunityRepository(conn)
        wb_sync = OpportunityWorkbookSync(Path(self.workbook_path))

        summary = {
            "status": "COMPLETED",
            "started_at": start_time.isoformat(),
            "adapters": {},
            "new_opportunities_found": 0,
            "total_qualified": 0,
            "errors": []
        }

        try:
            for adapter in self.adapters:
                adp_name = adapter.adapter_name
                logger.info(f"Executing adapter: {adp_name}")

                # Load checkpoint
                chk = repo.get_checkpoint(adp_name)
                if not chk:
                    chk = DiscoveryCheckpoint(
                        source_key=adp_name,
                        category=adapter.category,
                        cursor_or_token="0",
                        items_discovered_count=0,
                        status="INITIAL"
                    )

                try:
                    raw_items = adapter.fetch_raw_opportunities(checkpoint=chk, limit=30)
                    new_in_adapter = 0

                    for item in raw_items:
                        try:
                            org, opp = adapter.normalize_raw_item(item)
                            repo.save_organization(org)

                            # Deduplication check
                            is_dup = repo.is_opportunity_duplicate(
                                opp.opportunity_id,
                                opp.application_url,
                                opp.contact_email
                            )

                            # Score and audit
                            opp = OpportunityRanker.score_opportunity(opp, org)
                            opp = audit_opportunity(opp, org)

                            repo.save_opportunity(opp)

                            if not is_dup:
                                new_in_adapter += 1
                                summary["new_opportunities_found"] += 1

                        except Exception as item_err:
                            logger.warning(f"Error normalizing item in {adp_name}: {item_err}")

                    # Update checkpoint
                    current_cursor = int(chk.cursor_or_token or "0")
                    chk.cursor_or_token = str(current_cursor + len(raw_items))
                    chk.items_discovered_count += new_in_adapter
                    chk.last_queried_at = datetime.now(timezone.utc).isoformat()
                    chk.status = "SUCCESS"
                    repo.save_checkpoint(chk)

                    summary["adapters"][adp_name] = {
                        "items_fetched": len(raw_items),
                        "new_items": new_in_adapter,
                        "checkpoint_cursor": chk.cursor_or_token
                    }

                except Exception as adp_err:
                    err_msg = f"Failed adapter {adp_name}: {adp_err}"
                    logger.error(err_msg)
                    summary["errors"].append(err_msg)
                    if chk:
                        chk.status = "ERROR"
                        chk.error_message = str(adp_err)
                        repo.save_checkpoint(chk)

            # Re-evaluate all opportunities in database for Excel synchronization
            all_opps = repo.list_opportunities(min_score=40.0, limit=500)
            summary["total_qualified"] = len(all_opps)

            orgs_by_id: Dict[str, Organization] = {}
            for o in all_opps:
                if o.organization_id not in orgs_by_id:
                    org_obj = repo.get_organization(o.organization_id)
                    if org_obj:
                        orgs_by_id[o.organization_id] = org_obj

            # Synchronize Workbook atomically
            wb_sync.initialize_opportunity_sheets()
            wb_sync.sync_opportunities(all_opps, orgs_by_id)

            sched_records = OpportunityScheduler.generate_unified_schedule(all_opps, orgs_by_id)
            sched_count = wb_sync.sync_unified_schedule(sched_records)
            summary["unified_schedule_count"] = sched_count

            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            summary["completed_at"] = end_time.isoformat()
            summary["duration_seconds"] = round(duration, 2)

            logger.info(
                f"Discovery cycle finished in {duration:.1f}s: "
                f"{summary['new_opportunities_found']} new opps, {len(all_opps)} qualified synchronized."
            )

            audit_logger.log_event(
                event_type="RECURRING_DISCOVERY_CYCLE_COMPLETED",
                actor="RECURRING_WORKER",
                entity_type="DISCOVERY_SWEEP",
                entity_id=start_time.isoformat(),
                details=summary
            )

            return summary

        finally:
            conn.close()

    def run_daemon(self) -> None:
        """
        Runs continuously as a persistent recurring daemon with signal handling and restart recovery.
        """
        self.running = True

        def _handle_exit(sig, frame):
            logger.info(f"Received shutdown signal ({sig}). Stopping RecurringDiscoveryWorker gracefully...")
            self.running = False

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        logger.info(f"RecurringDiscoveryWorker daemon started (interval: {self.interval_seconds}s)")

        while self.running:
            try:
                self.execute_discovery_cycle()
            except Exception as e:
                logger.error(f"Unexpected error in discovery cycle: {e}", exc_info=True)

            logger.info(f"Sleeping for {self.interval_seconds} seconds until next scheduled run...")
            slept = 0
            while self.running and slept < self.interval_seconds:
                time.sleep(1.0)
                slept += 1

        logger.info("RecurringDiscoveryWorker daemon stopped cleanly.")
