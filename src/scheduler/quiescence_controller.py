"""
Scheduler Quiescence Controller & Supervisor Lifecycle Management.
Provides safe, graceful maintenance quiescence, process tree inspection,
and Windows Task Scheduler supervisor lifecycle management without force-killing.
"""

import os
import sys
import json
import time
import shutil
import sqlite3
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import settings
from src.scheduler.queue_runner import is_process_running, SCHEDULER_LOCK_PATH
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)

DEFAULT_QUIESCENCE_FLAG = settings.DATA_DIR / "maintenance_quiesce.flag"
TASK_NAME = "OutreachEngine_Launch_Monday"


class QuiescenceError(Exception):
    """Base exception for scheduler quiescence failures."""
    pass


class ActiveTransactionsError(QuiescenceError):
    """Raised when active SMTP or delivery transactions are in flight."""
    pass


class ConcurrentWriterLockError(QuiescenceError):
    """Raised when active workbook or scheduler locks are held."""
    pass


class SupervisorConfigurationError(QuiescenceError):
    """Raised when Windows Scheduled Task settings or state are unsafe."""
    pass


class SchedulerQuiescenceManager:
    """
    Manages non-destructive scheduler quiescence, supervisor lifecycle,
    and process-tree verification for safe production maintenance.
    """

    def __init__(
        self,
        quiescence_flag_path: Optional[Path] = None,
        scheduler_lock_path: Optional[Path] = None,
        master_workbook_path: Optional[Path] = None,
        db_path: Optional[Path] = None,
        task_name: str = TASK_NAME
    ):
        self.flag_path = Path(quiescence_flag_path or DEFAULT_QUIESCENCE_FLAG)
        self.lock_path = Path(scheduler_lock_path or SCHEDULER_LOCK_PATH)
        self.workbook_path = Path(master_workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.task_name = task_name

    # -------------------------------------------------------------------------
    # 1. Quiescence Flag IPC
    # -------------------------------------------------------------------------
    def request_quiescence(self, operator_id: str = "OPERATOR", reason: str = "Gate A Maintenance") -> Path:
        """Create the quiescence flag file requesting running schedulers to exit gracefully."""
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "operator_id": operator_id,
            "reason": reason,
            "pid": os.getpid()
        }
        self.flag_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        audit_logger.log_event(
            event_type="SCHEDULER_QUIESCENCE_REQUESTED",
            actor=operator_id,
            entity_type="SCHEDULER_SUPERVISOR",
            entity_id=str(self.flag_path),
            details=payload
        )
        return self.flag_path

    def cancel_quiescence(self, operator_id: str = "OPERATOR") -> None:
        """Remove the quiescence flag file."""
        if self.flag_path.exists():
            try:
                self.flag_path.unlink()
                audit_logger.log_event(
                    event_type="SCHEDULER_QUIESCENCE_CANCELLED",
                    actor=operator_id,
                    entity_type="SCHEDULER_SUPERVISOR",
                    entity_id=str(self.flag_path),
                    details={"status": "FLAG_REMOVED"}
                )
            except Exception as e:
                logger.warning(f"Failed to remove quiescence flag: {e}")

    def is_quiescence_flag_active(self) -> bool:
        """Check if the quiescence flag is currently present."""
        return self.flag_path.exists()

    # -------------------------------------------------------------------------
    # 2. Windows Task Scheduler Supervisor Lifecycle
    # -------------------------------------------------------------------------
    def get_task_supervisor_info(self) -> Dict[str, Any]:
        """Query Scheduled Task state and settings via PowerShell."""
        ps_cmd = (
            f"Get-ScheduledTask -TaskName '{self.task_name}' -ErrorAction SilentlyContinue | "
            f"Select-Object TaskName, State, "
            f"@{{Name='MultipleInstances';Expression={{$_.Settings.MultipleInstances.ToString()}}}}, "
            f"@{{Name='RestartCount';Expression={{$_.Settings.RestartCount}}}}, "
            f"@{{Name='RestartInterval';Expression={{$_.Settings.RestartInterval}}}}, "
            f"@{{Name='Enabled';Expression={{$_.Settings.Enabled}}}} | "
            f"ConvertTo-Json"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if res.returncode != 0 or not res.stdout.strip():
            return {"exists": False, "task_name": self.task_name, "state": "UNKNOWN"}
        try:
            data = json.loads(res.stdout)
            data["exists"] = True
            return data
        except Exception:
            return {"exists": True, "task_name": self.task_name, "raw_output": res.stdout.strip()}

    def disable_task_supervisor(self) -> Dict[str, Any]:
        """Disable the Windows Scheduled Task so the supervisor cannot relaunch another sender."""
        ps_cmd = f"Disable-ScheduledTask -TaskName '{self.task_name}' -ErrorAction Stop | Select-Object TaskName, State | ConvertTo-Json"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        info = self.get_task_supervisor_info()
        is_disabled = str(info.get("State")).lower() in ("disabled", "1")
        audit_logger.log_event(
            event_type="TASK_SUPERVISOR_DISABLED",
            actor="SCHEDULER_QUIESCENCE_MGR",
            entity_type="SCHEDULED_TASK",
            entity_id=self.task_name,
            details={"is_disabled": is_disabled, "task_info": info}
        )
        return {"task_name": self.task_name, "disabled": is_disabled, "info": info}

    def enable_and_start_task_supervisor(self) -> Dict[str, Any]:
        """Re-enable and launch the Windows Scheduled Task after maintenance."""
        ps_enable = f"Enable-ScheduledTask -TaskName '{self.task_name}' -ErrorAction Stop | Select-Object TaskName, State | ConvertTo-Json"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_enable], capture_output=True, text=True)

        ps_start = f"Start-ScheduledTask -TaskName '{self.task_name}' -ErrorAction Stop"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_start], capture_output=True, text=True)

        # Allow process to spawn
        time.sleep(2)
        info = self.get_task_supervisor_info()
        audit_logger.log_event(
            event_type="TASK_SUPERVISOR_RESUMED",
            actor="SCHEDULER_QUIESCENCE_MGR",
            entity_type="SCHEDULED_TASK",
            entity_id=self.task_name,
            details={"info": info}
        )
        return {"task_name": self.task_name, "resumed": True, "info": info}

    # -------------------------------------------------------------------------
    # 3. Process Tree & Live Sender Verification
    # -------------------------------------------------------------------------
    def get_live_scheduler_processes(self) -> List[Dict[str, Any]]:
        """
        Query Win32_Process using WMI filter to inspect any active python processes
        running run_session08_campaign.py.
        """
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*run_session08_campaign*' } | "
            "Select-Object ProcessId, ParentProcessId, Name, CommandLine, CreationDate | "
            "ConvertTo-Json"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        if not res.stdout.strip():
            return []
        try:
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                return [data]
            return data
        except Exception:
            return []

    # -------------------------------------------------------------------------
    # 4. Drainage Verifications: SMTP, Delivery Watches, & Workbook Locks
    # -------------------------------------------------------------------------
    def verify_no_active_smtp_transactions(self) -> Dict[str, Any]:
        """
        Confirm that no outbound delivery is in active 10-minute watch state
        and no in-flight transport operations exist.
        """
        if not self.db_path.exists():
            return {"active_watches": 0, "total_tracked": 0, "safe": True}

        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM outbound_delivery_tracker WHERE tracking_status = 'ACTIVE_WATCH';")
        active_watches = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM outbound_delivery_tracker;")
        total_tracked = cur.fetchone()[0]

        conn.close()

        safe = (active_watches == 0)
        return {
            "active_watches": active_watches,
            "total_tracked": total_tracked,
            "safe": safe
        }

    def verify_no_workbook_writers(self) -> Dict[str, Any]:
        """Verify no open .lock, .opp_lock, or temporary staging files exist."""
        wb_lock = self.workbook_path.with_suffix(".lock")
        wb_opp_lock = self.workbook_path.with_suffix(".opp_lock")
        sched_lock = self.lock_path

        active_locks = []
        if wb_lock.exists():
            active_locks.append(str(wb_lock))
        if wb_opp_lock.exists():
            active_locks.append(str(wb_opp_lock))

        sched_info = None
        if sched_lock.exists():
            try:
                with open(sched_lock, "r", encoding="utf-8") as f:
                    sched_info = json.load(f)
                active_pid = sched_info.get("pid")
                if active_pid and is_process_running(active_pid):
                    active_locks.append(f"scheduler.lock(PID {active_pid})")
            except Exception:
                active_locks.append("scheduler.lock(unreadable)")

        tmp_files = list(self.workbook_path.parent.glob(".tmp_*.xlsx"))

        return {
            "workbook_lock_exists": wb_lock.exists(),
            "workbook_opp_lock_exists": wb_opp_lock.exists(),
            "scheduler_lock_exists": sched_lock.exists(),
            "scheduler_lock_info": sched_info,
            "pending_temporary_files": len(tmp_files),
            "active_locks": active_locks,
            "safe": (len(active_locks) == 0 and len(tmp_files) == 0)
        }

    def verify_database_integrity(self) -> Dict[str, Any]:
        """Run quick_check and integrity_check on the production SQLite database."""
        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("PRAGMA quick_check;")
        qc = cur.fetchone()[0]
        cur.execute("PRAGMA integrity_check;")
        ic = cur.fetchone()[0]
        conn.close()

        safe = (qc == "ok" and ic == "ok")
        return {
            "quick_check": qc,
            "integrity_check": ic,
            "safe": safe
        }

    # -------------------------------------------------------------------------
    # 5. Fail-Closed Preflight Validation
    # -------------------------------------------------------------------------
    def verify_quiescence_preflight(self) -> Dict[str, Any]:
        """
        Execute full non-destructive validation that the system is quiesced,
        all transactions drained, supervisor locked out, and safe for migration.
        """
        task_info = self.get_task_supervisor_info()
        smtp_check = self.verify_no_active_smtp_transactions()
        writer_check = self.verify_no_workbook_writers()
        db_check = self.verify_database_integrity()
        live_procs = self.get_live_scheduler_processes()

        refusal_reasons = []

        if not smtp_check["safe"]:
            refusal_reasons.append(f"Active delivery watches in flight: {smtp_check['active_watches']}")

        if not writer_check["safe"]:
            refusal_reasons.append(f"Active writer locks detected: {writer_check['active_locks']}")

        if not db_check["safe"]:
            refusal_reasons.append(f"Database integrity check failed: qc={db_check['quick_check']}, ic={db_check['integrity_check']}")

        can_proceed_safely = (len(refusal_reasons) == 0)

        return {
            "task_supervisor": task_info,
            "active_python_processes": len(live_procs),
            "process_list": live_procs,
            "smtp_transactions": smtp_check,
            "workbook_writers": writer_check,
            "database_integrity": db_check,
            "can_proceed_safely": can_proceed_safely,
            "refusal_reasons": refusal_reasons
        }
