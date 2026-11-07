"""
Opportunity Intelligence Engine: Unified Campaign Runner & Execution Engine (Session 11).

Architecture & Invariants:
1. Shared Scheduling Architecture:
   - Integrates the 1,390 existing production campaign professors with multi-category
     discovered opportunities (academic, startup, frontier industry, institutional).
   - Serves recipients strictly within their active regional time block (BLOCK_01 to BLOCK_13)
     and recipient-local working days.
2. Channel & Portal Boundary:
   - Portal-routed applications are permanently isolated to the Application Tracker
     and NEVER passed to the SMTP dispatcher.
3. Shared Dispatch Ledger & Quota Cadence:
   - Shared single-instance enforcement via data/scheduler.lock.
   - 10 emails per batch across all categories combined.
   - 300-second (5 min) cooling break between batches.
   - 25-second minimum spacing between individual messages.
   - 200 emails / day application ceiling (overridden by IITD server limits).
   - 900-second cooldown on account rate limits.
4. Fresh Pre-Dispatch Check:
   - Real-time read-only query to data/outreach_engine.db immediately before dispatch.
   - Rejects previously contacted, bounced, or suppressed recipients.
5. Ambiguous Delivery Safeguard:
   - Ambiguous SMTP outcomes (timeouts during/after DATA transfer) are marked
     AMBIGUOUS_DELIVERY and never automatically retried.
6. Absolute Production Protection:
   - Operates in simulation/dry-run mode unless explicit operator activation is provided.
"""

import os
import sys
import time
import logging
import sqlite3
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings
from src.opportunities.models import Opportunity, Organization, ContactHistoryStatus
from src.opportunities.unified_queue import UnifiedOutreachQueueManager, UnifiedQueueItem
from src.opportunities.budget_and_tracking import GlobalBudgetManager
from src.opportunities.timezone_engine import DynamicTimezoneEngine, CanonicalRegionalBlockRegistry
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.scheduler.queue_runner import TIME_BLOCK_WINDOWS, is_process_running
from src.security.audit_logger import audit_logger

logger = logging.getLogger("unified_campaign_runner")


class UnifiedCampaignRunner:
    """
    Unified multi-category campaign runner operating under shared scheduling,
    shared budget limits, and strict delivery gating.
    """

    def __init__(
        self,
        dev_db_path: Optional[Path] = None,
        prod_db_path: Optional[Path] = None,
        lock_file_path: Optional[Path] = None,
        budget_manager: Optional[GlobalBudgetManager] = None
    ):
        if dev_db_path is None:
            self.dev_db_path = settings.SQLITE_DB_PATH if settings.SQLITE_DB_PATH.exists() else (settings.DATA_DIR / "development" / "opportunity_dev.db")
        else:
            self.dev_db_path = dev_db_path
        self.prod_db_path = prod_db_path or settings.SQLITE_DB_PATH
        self.lock_file_path = lock_file_path or (settings.DATA_DIR / "scheduler.lock")
        self.ist_tz = ZoneInfo("Asia/Kolkata")

        self.dev_conn = sqlite3.connect(str(self.dev_db_path))
        self.dev_conn.row_factory = sqlite3.Row

        self.budget_manager = budget_manager or GlobalBudgetManager()
        self.queue_manager = UnifiedOutreachQueueManager(
            dev_conn=self.dev_conn,
            prod_db_path=self.prod_db_path,
            budget_manager=self.budget_manager
        )
        self.lock_acquired = False

    def acquire_lock(self, force: bool = False) -> bool:
        """Acquires the scheduler lockfile ensuring single active sender."""
        if self.lock_file_path.exists():
            try:
                content = self.lock_file_path.read_text(encoding="utf-8").strip()
                existing_pid = int(content) if content.isdigit() else None
                if existing_pid and is_process_running(existing_pid):
                    if not force:
                        logger.warning(f"Scheduler lock held by running PID {existing_pid}. Cannot acquire.")
                        return False
            except Exception as e:
                logger.warning(f"Error reading lockfile: {e}")

        try:
            self.lock_file_path.write_text(str(os.getpid()), encoding="utf-8")
            self.lock_acquired = True
            logger.info(f"Acquired scheduler lock under PID {os.getpid()}.")
            return True
        except Exception as e:
            logger.error(f"Failed to acquire scheduler lock: {e}")
            return False

    def release_lock(self) -> None:
        """Releases the scheduler lockfile if owned by this process."""
        if self.lock_acquired and self.lock_file_path.exists():
            try:
                content = self.lock_file_path.read_text(encoding="utf-8").strip()
                if content == str(os.getpid()):
                    self.lock_file_path.unlink(missing_ok=True)
                    self.lock_acquired = False
                    logger.info("Released scheduler lock.")
            except Exception as e:
                logger.warning(f"Error releasing scheduler lock: {e}")

    def get_active_production_block(self, now_ist: Optional[datetime] = None) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Determines which of the 13 canonical production blocks is currently active.
        """
        if now_ist is None:
            now_ist = datetime.now(self.ist_tz)

        current_time = now_ist.strftime("%H:%M")
        for block_id, window in TIME_BLOCK_WINDOWS.items():
            start = window["start_ist"]
            end = window["end_ist"]
            if start <= current_time < end:
                return block_id, window
        return None

    def find_eligible_candidates_in_block(
        self,
        block_id: str,
        limit: int = 10
    ) -> List[UnifiedQueueItem]:
        """
        Finds queued candidates assigned to the given block.
        Prioritizes by category: Academic leads -> Startups -> Frontier Industry -> Institutions.
        """
        cur = self.dev_conn.cursor()
        cur.execute("""
            SELECT queue_id FROM unified_outreach_queue
            WHERE assigned_block_id = ?
              AND queue_status = 'QUEUED'
              AND delivery_channel = 'SMTP_GATEWAY'
            ORDER BY 
                CASE category
                    WHEN 'ACADEMIC_LAB' THEN 1
                    WHEN 'STARTUP' THEN 2
                    WHEN 'FRONTIER_COMPANY' THEN 3
                    WHEN 'INSTITUTIONAL_PROGRAM' THEN 4
                    ELSE 5
                END,
                created_at ASC
            LIMIT ?;
        """, (block_id, limit))
        rows = cur.fetchall()

        items: List[UnifiedQueueItem] = []
        for r in rows:
            item = self.queue_manager.get_queue_item(r["queue_id"])
            if item:
                items.append(item)
        return items

    def run_batch_simulation(
        self,
        count: int = 12,
        cooling_seconds: int = 300,
        simulated_cooling: bool = True,
        now_utc: Optional[datetime] = None,
        block_id: Optional[str] = "BLOCK_07"
    ) -> Dict[str, Any]:
        """
        Executes a controlled multi-category dispatch sequence demonstrating:
        1. Multi-category traversal (academic, startup, etc.)
        2. Real-time pre-dispatch verification
        3. 10-email batch boundary and 300s cooling break
        4. Resumption on Batch 2
        5. Ambiguous delivery safeguard demonstration
        """
        if now_utc is None:
            # Default to active Monday morning window (08:00 UTC = 09:00 CET for BLOCK_07)
            now_utc = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)

        from datetime import timedelta
        sim_now = now_utc

        # In simulation mode, authorize categories for the demonstration
        self.budget_manager.authorize_category("STARTUP")
        self.budget_manager.authorize_category("ACADEMIC_LAB")
        self.budget_manager.authorize_category("FRONTIER_COMPANY")
        self.budget_manager.authorize_category("INSTITUTIONAL_PROGRAM")

        results = {
            "batch_1_count": 0,
            "batch_2_count": 0,
            "cooling_break_enforced": False,
            "cooling_break_duration": cooling_seconds,
            "items_processed": [],
            "suppressed_collisions": 0,
            "ambiguous_delivery_handled": False,
            "daily_ceiling_enforced": False
        }

        # Query up to count eligible queued items in target block or all blocks
        cur = self.dev_conn.cursor()
        if block_id:
            cur.execute("""
                SELECT queue_id FROM unified_outreach_queue
                WHERE assigned_block_id = ?
                  AND queue_status = 'QUEUED'
                  AND delivery_channel = 'SMTP_GATEWAY'
                LIMIT ?;
            """, (block_id, count))
        else:
            cur.execute("""
                SELECT queue_id FROM unified_outreach_queue
                WHERE queue_status = 'QUEUED'
                  AND delivery_channel = 'SMTP_GATEWAY'
                LIMIT ?;
            """, (count,))
        rows = cur.fetchall()

        if not rows:
            return {"status": "NO_QUEUED_ITEMS", "results": results}

        current_batch = 1
        messages_in_batch = 0

        for r in rows:
            q_id = r["queue_id"]
            item = self.queue_manager.get_queue_item(q_id)
            if not item:
                continue

            # Check pre-dispatch at current simulated time
            authorized, reason = self.queue_manager.pre_dispatch_check(q_id, now_utc=sim_now)
            if not authorized:
                if "SUPPRESSED_COLLISION" in reason:
                    results["suppressed_collisions"] += 1
                continue

            # Dispatch item
            disp_res = self.queue_manager.dispatch_queue_item(q_id, simulated=True, now_utc=sim_now)
            results["items_processed"].append(disp_res)

            messages_in_batch += 1
            if current_batch == 1:
                results["batch_1_count"] += 1
            else:
                results["batch_2_count"] += 1

            # Advance simulated time by 26s to satisfy 25s per-message spacing
            sim_now += timedelta(seconds=26)

            # Batch boundary enforcement at exactly 10 messages
            if messages_in_batch >= 10:
                results["cooling_break_enforced"] = True
                audit_logger.log_event(
                    event_type="UNIFIED_BATCH_COOLING_BREAK",
                    actor="UNIFIED_CAMPAIGN_RUNNER",
                    entity_type="DISPATCH_BATCH",
                    entity_id=f"BATCH_{current_batch}",
                    details={"messages": messages_in_batch, "cooling_seconds": cooling_seconds}
                )
                if not simulated_cooling:
                    time.sleep(cooling_seconds)

                # Advance simulated time by cooling break
                sim_now += timedelta(seconds=cooling_seconds + 1)
                current_batch += 1
                messages_in_batch = 0

        # Demonstrate Ambiguous Delivery Safeguard
        # If an item encounters SMTPAmbiguousDeliveryError, it must transition to AMBIGUOUS_DELIVERY
        # and be permanently suppressed from auto-resending
        mock_ambiguous_id = rows[0]["queue_id"] if rows else "MOCK_AMB"
        cur.execute("""
            UPDATE unified_outreach_queue
            SET queue_status = 'AMBIGUOUS_DELIVERY',
                failure_reason = 'SMTPAmbiguousDeliveryError: Connection dropped after DATA command transmission.',
                updated_at = ?
            WHERE queue_id = ?;
        """, (datetime.now(timezone.utc).isoformat(), mock_ambiguous_id))
        self.dev_conn.commit()

        # Verify that pre_dispatch_check now permanently rejects this ambiguous item
        can_retry, retry_msg = self.queue_manager.pre_dispatch_check(mock_ambiguous_id)
        assert not can_retry, "Ambiguous delivery must NEVER be dispatchable!"
        results["ambiguous_delivery_handled"] = True

        # Daily limit ceiling check
        can_more, budget_reason = self.budget_manager.can_dispatch("ACADEMIC_LAB", 250)
        results["daily_ceiling_enforced"] = not can_more

        return results
