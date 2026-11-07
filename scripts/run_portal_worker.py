"""
Opportunity Intelligence Engine: Autonomous Portal Application Worker.
Handles autonomous stage 1-3 form filling, material preparation, and Stage 5 manual action checkpoint logging.
Fail-closed on Stage 4 live submission unless explicitly authorized by operator.
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    ContactChannel,
    ApplicationStatus
)
from src.opportunities.repository import OpportunityRepository
from src.opportunities.portal_automation import (
    PortalApplicationDriver,
    ApplicationStage,
    PortalSubmissionStatus,
    BlockingReason
)
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.security.audit_logger import audit_logger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("portal_worker")


class PortalApplicationWorker:
    """Coordinates autonomous portal preparation, form inspection, and manual queue routing."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        workbook_path: Optional[Path] = None,
        live_submission_enabled: bool = False
    ):
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.live_submission_enabled = live_submission_enabled

        self.conn = sqlite3.connect(str(self.db_path))
        self.repo = OpportunityRepository(self.conn)
        self.sync = OpportunityWorkbookSync(workbook_path=self.workbook_path)
        self.engine = PortalApplicationDriver()

    def run_portal_cycle(self) -> Dict[str, Any]:
        """Runs a complete portal qualification, preparation, and inspection sweep."""
        logger.info("Starting portal application automation cycle...")
        all_opps = self.repo.list_opportunities(min_score=0.0, limit=10000)

        # Filter portal-directed opportunities
        portal_opps = [
            opp for opp in all_opps
            if opp.hiring_contact_channel == ContactChannel.APPLICATION_PORTAL.value
            or (not opp.contact_email and bool(opp.application_url))
        ]

        logger.info(f"Identified {len(portal_opps)} portal-routed opportunities across all categories.")

        manual_checkpoints: List[Dict[str, Any]] = []
        prepared_drafts: List[Dict[str, Any]] = []
        inspected_count = 0

        for opp in portal_opps:
            inspected_count += 1
            org = self.repo.get_organization(opp.organization_id) or Organization(
                organization_id=opp.organization_id,
                name=opp.organization_id,
                category=opp.opportunity_type
            )

            # Stage 1-3: Workflow simulation & field inspection
            app_state = self.engine.simulate_application_flow(opp, org)

            if app_state.stage == ApplicationStage.STAGE_5_MANUAL_INTERVENTION_REQUIRED:
                cp = app_state.blocking_checkpoint
                manual_checkpoints.append({
                    "action_id": f"ACT-{cp.checkpoint_id[:10]}",
                    "opportunity_id": cp.opportunity_id,
                    "application_id": app_state.application_id,
                    "organization_name": org.name,
                    "blocking_reason": cp.blocking_reason.value,
                    "blocking_field_name": cp.blocking_field_name,
                    "prompt_or_instructions": cp.prompt_or_question,
                    "priority": cp.priority,
                    "status": cp.status,
                    "created_at": cp.created_at,
                    "resolved_at": None,
                    "resume_action": cp.resume_instructions
                })
            elif app_state.stage == ApplicationStage.STAGE_4_SUBMISSION_READY:
                # Stage 4 rehearsal (fail-closed simulation)
                rehearsal = self.engine.rehearse_stage4_controlled_submission(
                    app_state,
                    operator_authorization_granted=self.live_submission_enabled
                )
                prepared_drafts.append({
                    "opportunity_id": opp.opportunity_id,
                    "organization": org.name,
                    "title": opp.title,
                    "portal_url": opp.application_url,
                    "rehearsal_status": rehearsal["status"],
                    "real_submission_executed": rehearsal.get("real_submission_executed", False)
                })

        # Synchronize Manual Action Queue sheet if new checkpoints exist
        if manual_checkpoints:
            self.sync.sync_manual_action_queue(manual_checkpoints)
            logger.info(f"Recorded {len(manual_checkpoints)} manual intervention checkpoints to workbook.")

        audit_logger.log_event(
            event_type="PORTAL_WORKER_CYCLE_COMPLETED",
            actor="PORTAL_APPLICATION_WORKER",
            entity_type="SYSTEM",
            entity_id=str(os.getpid()),
            details={
                "portal_opps_total": len(portal_opps),
                "inspected_count": inspected_count,
                "manual_checkpoints_logged": len(manual_checkpoints),
                "prepared_drafts": len(prepared_drafts),
                "live_submission_enabled": self.live_submission_enabled
            }
        )

        return {
            "status": "COMPLETED",
            "portal_opps_total": len(portal_opps),
            "manual_checkpoints_logged": len(manual_checkpoints),
            "prepared_drafts": len(prepared_drafts),
            "live_submission_enabled": self.live_submission_enabled
        }


def main():
    parser = argparse.ArgumentParser(description="Opportunity Intelligence Engine: Portal Worker")
    parser.add_argument("--once", action="store_true", default=False, help="Run single portal processing sweep")
    parser.add_argument("--daemon", action="store_true", default=False, help="Run persistent recurring background loop")
    parser.add_argument("--interval", type=int, default=3600, help="Interval in seconds for daemon mode (default: 3600s)")
    parser.add_argument("--live", action="store_true", default=False, help="Authorize live portal submissions (FAIL-CLOSED default: False)")
    args = parser.parse_args()

    worker = PortalApplicationWorker(live_submission_enabled=args.live)

    if args.daemon:
        logger.info(f"Starting Portal Application Worker in daemon mode (interval: {args.interval}s)...")
        while True:
            try:
                worker.run_portal_cycle()
            except Exception as e:
                logger.error(f"Error during portal cycle: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        res = worker.run_portal_cycle()
        print(f"[PORTAL WORKER COMPLETE] Processed portal applications: {res}")


if __name__ == "__main__":
    main()
