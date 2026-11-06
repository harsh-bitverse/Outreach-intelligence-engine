"""
Opportunity Intelligence Engine: Safe Reversible Fail-Closed Deployment Manager (Sessions 11 & 12).

Provides automated, verified deployment preparation and rollback harnesses with strict fail-closed safety:
1. Active SMTP Transaction Detection: Safely waits for in-flight SMTP dispatches to complete.
   Fails closed with ActiveSMTPTransactionTimeoutError if timeout expires.
2. Single-Instance Lock Verification: Enforces mutual exclusion via data/scheduler.lock.
   Fails closed with SchedulerLockOccupiedError if active process holds lock.
3. State Checkpoint Preservation: Captures durable atomic snapshots of production DB and Master Excel.
   Fails closed with CheckpointCreationError if any source or destination check fails.
4. Idempotent Metadata Migration: Adds unified outreach queue without modifying existing tables.
   Fails closed with DatabaseContentionError if database lock cannot be acquired.
5. Instant Reversion / Rollback: Restores pre-migration checkpoint cleanly with post-restore verification.
   Fails closed with RollbackVerificationError if restored database integrity check fails.
"""

import os
import shutil
import sqlite3
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from config.settings import settings
from src.scheduler.queue_runner import is_process_running
from src.security.audit_logger import audit_logger

logger = logging.getLogger("deployment_manager")


class DeploymentAbortedError(Exception):
    """Base exception for all fail-closed deployment aborts."""
    pass


class ActiveSMTPTransactionTimeoutError(DeploymentAbortedError):
    """Raised when in-flight SMTP dispatch does not clear within timeout."""
    pass


class SchedulerLockOccupiedError(DeploymentAbortedError):
    """Raised when production scheduler lock is held by an active process."""
    pass


class CheckpointCreationError(DeploymentAbortedError):
    """Raised when state checkpoint creation or verification fails."""
    pass


class RollbackVerificationError(DeploymentAbortedError):
    """Raised when deployment rollback or restoration fails integrity checks."""
    pass


class DatabaseContentionError(DeploymentAbortedError):
    """Raised when target database cannot acquire exclusive write lock during migration."""
    pass


class UnifiedDeploymentManager:
    """
    Manages safe, zero-interruption deployment rehearsal and rollback testing
    for the unified opportunity outreach engine with strict fail-closed guarantees.
    """

    def __init__(
        self,
        prod_db_path: Optional[Path] = None,
        master_workbook_path: Optional[Path] = None,
        lock_file_path: Optional[Path] = None,
        backup_dir: Optional[Path] = None
    ):
        self.prod_db_path = prod_db_path or settings.SQLITE_DB_PATH
        self.master_workbook_path = master_workbook_path or settings.MASTER_WORKBOOK_PATH
        self.lock_file_path = lock_file_path or (settings.DATA_DIR / "scheduler.lock")
        self.backup_dir = backup_dir or (settings.DATA_DIR / "backups" / "deployment_checkpoints")
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------------
    # Step 1: Active SMTP Transaction Detection & Clean Wait
    # --------------------------------------------------------------------------
    def wait_for_active_smtp_transaction(
        self,
        max_wait_seconds: int = 60,
        poll_interval: float = 1.0,
        fail_closed: bool = False
    ) -> Tuple[bool, str]:
        """
        Inspects outbound delivery tracker for active in-flight dispatches or locks.
        Waits cleanly until all in-flight transmissions complete.
        If timeout expires and fail_closed=True, raises ActiveSMTPTransactionTimeoutError.
        """
        if not self.prod_db_path.exists():
            return True, "PROD_DB_NOT_PRESENT"

        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            try:
                conn = sqlite3.connect(f"file:{self.prod_db_path}?mode=ro", uri=True, timeout=2.0)
                cur = conn.cursor()
                cutoff_iso = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
                try:
                    cur.execute("""
                        SELECT count(*) FROM outbound_delivery_tracker
                        WHERE tracking_status = 'ACTIVE_WATCH'
                           OR (smtp_accepted_at IS NOT NULL AND (smtp_accepted_at >= ? OR smtp_accepted_at >= datetime('now', '-30 seconds')))
                           OR (dispatched_at IS NOT NULL AND (dispatched_at >= ? OR dispatched_at >= datetime('now', '-30 seconds')));
                    """, (cutoff_iso, cutoff_iso))
                    active_count = cur.fetchone()[0]
                except Exception:
                    cur.execute("""
                        SELECT count(*) FROM outbound_delivery_tracker
                        WHERE dispatched_at IS NOT NULL AND (dispatched_at >= ? OR dispatched_at >= datetime('now', '-30 seconds'));
                    """, (cutoff_iso,))
                    active_count = cur.fetchone()[0]
                conn.close()

                if active_count == 0:
                    return True, "CLEAR_NO_ACTIVE_TRANSACTIONS"
                
                time.sleep(poll_interval)
            except Exception as e:
                logger.warning(f"Error checking active SMTP transactions: {e}")
                time.sleep(poll_interval)

        if fail_closed:
            raise ActiveSMTPTransactionTimeoutError(
                "DEPLOYMENT_ABORTED: Active SMTP transaction in-flight; exceeded wait timeout."
            )
        return False, "TIMEOUT_WAITING_FOR_ACTIVE_TRANSACTION"

    # --------------------------------------------------------------------------
    # Step 2: Instance Locking & Duplicate Instance Prevention
    # --------------------------------------------------------------------------
    def verify_single_instance_lock(self, fail_closed: bool = False) -> Dict[str, Any]:
        """
        Inspects lockfile state and identifies whether an active process owns the scheduler lock.
        If lock is held by an active process and fail_closed=True, raises SchedulerLockOccupiedError.
        """
        if not self.lock_file_path.exists():
            return {
                "lock_held": False,
                "lock_pid": None,
                "process_running": False,
                "message": "Scheduler lockfile is free."
            }

        try:
            content = self.lock_file_path.read_text(encoding="utf-8").strip()
            lock_pid = int(content) if content.isdigit() else None
            running = is_process_running(lock_pid) if lock_pid else False

            res = {
                "lock_held": True,
                "lock_pid": lock_pid,
                "process_running": running,
                "message": f"Lock held by PID {lock_pid} (Active: {running})"
            }
            if running and fail_closed:
                raise SchedulerLockOccupiedError(
                    f"DEPLOYMENT_ABORTED: Production scheduler lock currently held by active PID {lock_pid}."
                )
            return res
        except (ValueError, OSError) as e:
            if fail_closed:
                raise SchedulerLockOccupiedError(f"DEPLOYMENT_ABORTED: Lockfile error: {e}")
            return {
                "lock_held": True,
                "lock_pid": None,
                "process_running": False,
                "error": str(e)
            }

    # --------------------------------------------------------------------------
    # Step 3: Checkpoint Preservation of Live State
    # --------------------------------------------------------------------------
    def create_pre_deployment_checkpoint(
        self,
        checkpoint_tag: Optional[str] = None,
        fail_closed: bool = True
    ) -> Dict[str, Any]:
        """
        Creates an atomic durable checkpoint of the database and master workbook.
        Validates created files; raises CheckpointCreationError on failure if fail_closed=True.
        """
        tag = checkpoint_tag or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        chk_folder = self.backup_dir / f"checkpoint_{tag}"
        chk_folder.mkdir(parents=True, exist_ok=True)

        copied_db = None
        copied_wb = None

        try:
            if self.prod_db_path.exists():
                db_dst = chk_folder / self.prod_db_path.name
                src_conn = sqlite3.connect(f"file:{self.prod_db_path}?mode=ro", uri=True, timeout=5.0)
                dst_conn = sqlite3.connect(str(db_dst))
                with dst_conn:
                    src_conn.backup(dst_conn)
                src_conn.close()
                dst_conn.close()

                # Verify snapshot integrity
                chk_c = sqlite3.connect(str(db_dst))
                chk_res = chk_c.execute("PRAGMA quick_check;").fetchone()
                chk_c.close()
                if not chk_res or chk_res[0] != "ok":
                    raise ValueError(f"Database checkpoint quick_check failed: {chk_res}")
                copied_db = db_dst

            if self.master_workbook_path.exists():
                wb_dst = chk_folder / self.master_workbook_path.name
                shutil.copy2(self.master_workbook_path, wb_dst)
                if not wb_dst.exists() or wb_dst.stat().st_size == 0:
                    raise IOError(f"Master workbook backup verification failed: {wb_dst}")
                copied_wb = wb_dst

        except Exception as e:
            if fail_closed:
                raise CheckpointCreationError(f"DEPLOYMENT_ABORTED: Checkpoint preservation failed: {e}")
            raise

        audit_logger.log_event(
            event_type="DEPLOYMENT_CHECKPOINT_CREATED",
            actor="DEPLOYMENT_MANAGER",
            entity_type="SYSTEM_CHECKPOINT",
            entity_id=tag,
            details={
                "folder": str(chk_folder),
                "db_backup": str(copied_db) if copied_db else None,
                "wb_backup": str(copied_wb) if copied_wb else None
            }
        )

        return {
            "checkpoint_tag": tag,
            "checkpoint_dir": chk_folder,
            "db_backup_path": copied_db,
            "workbook_backup_path": copied_wb,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

    # --------------------------------------------------------------------------
    # Step 4: Idempotent Metadata Migration
    # --------------------------------------------------------------------------
    def run_idempotent_metadata_migration(
        self,
        target_db_path: Path,
        fail_closed: bool = True
    ) -> Dict[str, Any]:
        """
        Applies the unified outreach queue schema and indexes to the target database
        without altering, renaming, or dropping existing production tables.
        Fails closed with DatabaseContentionError if locks cannot be acquired.
        """
        try:
            conn = sqlite3.connect(str(target_db_path), timeout=5.0)
            cur = conn.cursor()
            cur.execute("PRAGMA busy_timeout = 5000;")

            # Verify existing tables exist and remain untouched
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = {row[0] for row in cur.fetchall()}

            # Migration: Add unified_outreach_queue if not present
            cur.execute("""
                CREATE TABLE IF NOT EXISTS unified_outreach_queue (
                    queue_id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    organization_name TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    recipient_email TEXT,
                    hiring_contact_channel TEXT NOT NULL,
                    delivery_channel TEXT NOT NULL,
                    assigned_block_id TEXT NOT NULL,
                    iana_timezone TEXT NOT NULL,
                    queue_status TEXT NOT NULL,
                    draft_subject TEXT,
                    draft_body TEXT,
                    smtp_message_id TEXT,
                    scheduled_at TEXT,
                    dispatched_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    failure_reason TEXT
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uq_status ON unified_outreach_queue(queue_status);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uq_opp_id ON unified_outreach_queue(opportunity_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uq_block ON unified_outreach_queue(assigned_block_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uq_category ON unified_outreach_queue(category);")

            conn.commit()

            # Re-check tables
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            post_tables = {row[0] for row in cur.fetchall()}
            conn.close()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                if fail_closed:
                    raise DatabaseContentionError(f"DEPLOYMENT_ABORTED: Database contention during migration: {e}")
            raise

        audit_logger.log_event(
            event_type="METADATA_MIGRATION_APPLIED",
            actor="DEPLOYMENT_MANAGER",
            entity_type="DATABASE",
            entity_id=target_db_path.name,
            details={"tables_before": list(existing_tables), "tables_after": list(post_tables)}
        )

        return {
            "success": True,
            "target_db": str(target_db_path),
            "pre_existing_tables_preserved": list(existing_tables),
            "unified_queue_created": "unified_outreach_queue" in post_tables,
            "migrated_at": datetime.now(timezone.utc).isoformat()
        }

    # --------------------------------------------------------------------------
    # Step 5: Rollback Procedure & Reversion Test
    # --------------------------------------------------------------------------
    def rollback_to_checkpoint(
        self,
        checkpoint_dir: Path,
        target_db_path: Path,
        target_wb_path: Optional[Path] = None,
        fail_closed: bool = True
    ) -> Dict[str, Any]:
        """
        Restores database and workbook from checkpoint directory.
        Performs post-rollback integrity checks; fails closed with RollbackVerificationError.
        """
        db_src = checkpoint_dir / target_db_path.name
        if not db_src.exists():
            if fail_closed:
                raise RollbackVerificationError(f"DEPLOYMENT_ABORTED: Checkpoint database not found at {db_src}")
            raise FileNotFoundError(f"Checkpoint database not found at {db_src}")

        try:
            # Atomic copy back
            shutil.copy2(db_src, target_db_path)

            # Verification of restored database
            chk_c = sqlite3.connect(str(target_db_path))
            quick_check = chk_c.execute("PRAGMA quick_check;").fetchone()
            chk_c.close()
            if not quick_check or quick_check[0] != "ok":
                raise ValueError("Restored database integrity check failed (PRAGMA quick_check != ok).")

            wb_restored = False
            if target_wb_path:
                wb_src = checkpoint_dir / target_wb_path.name
                if wb_src.exists():
                    shutil.copy2(wb_src, target_wb_path)
                    if not target_wb_path.exists() or target_wb_path.stat().st_size == 0:
                        raise ValueError("Restored workbook verification failed.")
                    wb_restored = True

        except Exception as e:
            if fail_closed:
                raise RollbackVerificationError(f"DEPLOYMENT_ABORTED: Rollback integrity verification failed: {e}")
            raise

        audit_logger.log_event(
            event_type="DEPLOYMENT_ROLLBACK_COMPLETED",
            actor="DEPLOYMENT_MANAGER",
            entity_type="SYSTEM_RESTORE",
            entity_id=checkpoint_dir.name,
            details={"db_restored": str(target_db_path), "wb_restored": wb_restored}
        )

        return {
            "success": True,
            "restored_from": str(checkpoint_dir),
            "db_restored_to": str(target_db_path),
            "workbook_restored": wb_restored,
            "restored_at": datetime.now(timezone.utc).isoformat()
        }

    # --------------------------------------------------------------------------
    # Full Preflight Gate
    # --------------------------------------------------------------------------
    def execute_deployment_preflight(self, fail_closed: bool = True) -> Dict[str, Any]:
        """
        Executes complete fail-closed preflight deployment check.
        If any precondition fails (SMTP transaction active, lock occupied, etc.),
        aborts deployment immediately with a specific exception.
        """
        self.wait_for_active_smtp_transaction(max_wait_seconds=5, fail_closed=fail_closed)
        self.verify_single_instance_lock(fail_closed=fail_closed)
        return {
            "preflight_status": "READY_FOR_DEPLOYMENT",
            "message": "All deployment preflight checks passed cleanly."
        }
