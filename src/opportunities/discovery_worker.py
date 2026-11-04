"""
Opportunity Intelligence Engine: Independent Recurring Discovery Worker.

Orchestrates periodic discovery cycles across all four opportunity categories:
1. Academic Research Laboratories
2. Technical Startups
3. Frontier-Tech Companies & Industrial R&D
4. Publicly Funded Laboratories & National/International Programs

Maintains durable progress checkpoints, performs source grounding,
scores opportunities, synchronizes Excel sheets, and enforces single-worker safety.
Operates completely decoupled from the SMTP outreach loop.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from config.settings import settings
from src.opportunities.adapters import (
    BaseOpportunityAdapter,
    AcademicLabAdapter,
    StartupAdapter,
    FrontierIndustryAdapter,
    InstitutionalProgramAdapter
)
from src.opportunities.models import (
    Opportunity,
    Organization,
    DiscoveryRun
)
from src.opportunities.repository import OpportunityRepository
from src.opportunities.workbook_sync import OpportunityWorkbookSync
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)


class OpportunityDiscoveryWorker:
    """
    Background worker that runs autonomous discovery sweeps across all opportunity adapters.
    """

    def __init__(
        self,
        repo: OpportunityRepository,
        workbook_sync: Optional[OpportunityWorkbookSync] = None,
        lock_path: Optional[Path] = None,
        cli_binary_path: Optional[Path] = None
    ):
        self.repo = repo
        self.workbook_sync = workbook_sync or OpportunityWorkbookSync()
        self.lock_path = lock_path or (settings.DATA_DIR / "opportunity_discovery.lock")

        default_cli = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
        self.cli_binary_path = cli_binary_path or (default_cli if default_cli.exists() else Path(shutil.which("agy") or "agy"))

        # Register category adapters
        self.adapters: List[BaseOpportunityAdapter] = [
            AcademicLabAdapter(),
            StartupAdapter(),
            FrontierIndustryAdapter(),
            InstitutionalProgramAdapter()
        ]

    def acquire_lock(self, timeout_seconds: int = 10) -> bool:
        """Enforce single discovery worker instance."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({
                    "pid": os.getpid(),
                    "started_at": datetime.now(timezone.utc).isoformat()
                }).encode("utf-8"))
                os.close(fd)
                return True
            except FileExistsError:
                # Check for stale lock
                try:
                    with open(self.lock_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    pid = data.get("pid")
                    if pid and not self._is_pid_alive(pid):
                        self.lock_path.unlink()
                        continue
                except Exception:
                    pass
                time.sleep(0.5)
        raise TimeoutError(f"Another OpportunityDiscoveryWorker is currently active at {self.lock_path}")

    def release_lock(self) -> None:
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except OSError:
            pass

    def run_single_sweep(self, limit_per_adapter: int = 25) -> Dict[str, Any]:
        """
        Execute a full discovery pass across all registered opportunity adapters.
        """
        self.acquire_lock()
        sweep_start = datetime.now(timezone.utc)
        results: Dict[str, Any] = {
            "started_at": sweep_start.isoformat(),
            "adapters_run": [],
            "total_found": 0,
            "total_qualified": 0,
            "errors": []
        }

        try:
            all_discovered_opps: List[Opportunity] = []
            orgs_by_id: Dict[str, Organization] = {}

            for adapter in self.adapters:
                logger.info(f"Running discovery adapter: {adapter.adapter_name}")
                try:
                    run = adapter.run_discovery_cycle(self.repo, limit=limit_per_adapter)
                    results["adapters_run"].append(run.to_dict())
                    results["total_found"] += run.opportunities_found
                    results["total_qualified"] += run.opportunities_qualified
                except Exception as adp_err:
                    err_msg = f"Adapter {adapter.adapter_name} failed: {str(adp_err)}"
                    logger.error(err_msg)
                    results["errors"].append(err_msg)

            # Retrieve recently qualified opportunities to sync with Excel
            qualified_opps = self.repo.list_opportunities(min_score=50.0, limit=200)
            for opp in qualified_opps:
                if opp.organization_id not in orgs_by_id:
                    org = self.repo.get_organization(opp.organization_id)
                    if org:
                        orgs_by_id[opp.organization_id] = org

            # Synchronize to Workbook safely
            if self.workbook_sync:
                self.workbook_sync.initialize_opportunity_sheets()
                sync_stats = self.workbook_sync.sync_opportunities(qualified_opps, orgs_by_id)
                results["workbook_sync"] = sync_stats

            sweep_end = datetime.now(timezone.utc)
            results["completed_at"] = sweep_end.isoformat()
            results["duration_seconds"] = round((sweep_end - sweep_start).total_seconds(), 2)

            audit_logger.log_event(
                event_type="OPPORTUNITY_DISCOVERY_SWEEP_COMPLETED",
                actor="DISCOVERY_WORKER",
                entity_type="DISCOVERY_SWEEP",
                entity_id=sweep_start.isoformat(),
                details={
                    "total_found": results["total_found"],
                    "total_qualified": results["total_qualified"],
                    "duration_seconds": results["duration_seconds"]
                }
            )
            return results

        finally:
            self.release_lock()

    def query_antigravity_cli_research(self, topic_prompt: str, timeout: int = 60) -> Optional[str]:
        """
        Executes a targeted, non-interactive research query via the official Antigravity CLI.
        Grounds research without UI automation, private tokens, or web scraping fragility.
        """
        if not self.cli_binary_path.exists():
            logger.warning(f"Antigravity CLI binary not available at {self.cli_binary_path}")
            return None

        cmd = [
            str(self.cli_binary_path),
            "--effort", "low",
            "--disable-slash-commands",
            "--print", topic_prompt
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout.strip()
            else:
                logger.warning(f"CLI research query failed (code {proc.returncode}): {proc.stderr[:200]}")
                return None
        except Exception as e:
            logger.warning(f"CLI execution exception: {e}")
            return None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError):
                return False
