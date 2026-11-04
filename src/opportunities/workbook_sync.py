"""
Opportunity Intelligence Engine: Safe Multi-Sheet Workbook Synchronizer.

Preserves existing professor workbook sheets (`Time Blocks & Distribution`, `Professor Database`).
Safely initializes and synchronizes dedicated sheets:
1. `Startups`
2. `Companies & Industrial R&D`
3. `Institutional & National Labs`
4. `Unified Outreach Queue`
5. `Application Tracker`

Enforces crash-safety with atomic file replacement, pre-sync backups, and file locks.
"""

import os
import shutil
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    DiscoveryStatus,
    ContactChannel
)
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)


class OpportunityWorkbookSync:
    """
    Manages safe multi-sheet updates for the Opportunity Intelligence Engine.
    """

    NEW_SHEET_NAMES = [
        "Startups",
        "Companies & Industrial R&D",
        "Institutional & National Labs",
        "Unified Outreach Queue",
        "Application Tracker",
        "Unified Outreach Schedule"
    ]

    HEADER_FILL = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")  # Navy
    HEADER_FONT = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    DATA_FONT = Font(name="Calibri", size=10)
    BORDER = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9")
    )

    def __init__(self, workbook_path: Optional[Path] = None):
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.lock_path = self.workbook_path.with_suffix(".opp_lock")

    def acquire_lock(self, timeout_seconds: int = 15) -> None:
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}".encode("utf-8"))
                os.close(fd)
                return
            except FileExistsError:
                time.sleep(0.2)
        raise TimeoutError(f"Could not acquire workbook lock at {self.lock_path} within {timeout_seconds}s")

    def release_lock(self) -> None:
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except OSError:
            pass

    def create_backup(self) -> Path:
        backup_dir = settings.DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{self.workbook_path.stem}_opp_sync_{timestamp}.xlsx.bak"
        shutil.copy2(str(self.workbook_path), str(backup_path))
        return backup_path

    def initialize_opportunity_sheets(self) -> Dict[str, Any]:
        """
        Idempotently create and format the 5 dedicated opportunity sheets in the workbook.
        Guarantees existing sheets are untouched.
        """
        self.acquire_lock()
        try:
            self.create_backup()
            wb = openpyxl.load_workbook(str(self.workbook_path))

            created_sheets = []

            # 1. Startups Sheet
            if "Startups" not in wb.sheetnames:
                ws = wb.create_sheet(title="Startups")
                headers = [
                    "Rank", "Company Name", "Funding Stage", "Location / Timezone",
                    "Role Title", "Technical Domains", "Matched Project", "Compensation",
                    "Score", "Discovery Status", "Application Status", "Contact Name",
                    "Contact Email", "Official URL", "Next Action"
                ]
                self._format_header_row(ws, headers)
                created_sheets.append("Startups")

            # 2. Companies & Industrial R&D Sheet
            if "Companies & Industrial R&D" not in wb.sheetnames:
                ws = wb.create_sheet(title="Companies & Industrial R&D")
                headers = [
                    "Rank", "Organization Name", "Role / Placement Title", "Location / Timezone",
                    "Technical Domains", "Matched Project", "Compensation Details", "Eligibility Summary",
                    "Score", "Discovery Status", "Application Status", "Careers / Application URL",
                    "Deadline", "Next Action"
                ]
                self._format_header_row(ws, headers)
                created_sheets.append("Companies & Industrial R&D")

            # 3. Institutional & National Labs Sheet
            if "Institutional & National Labs" not in wb.sheetnames:
                ws = wb.create_sheet(title="Institutional & National Labs")
                headers = [
                    "Rank", "Institution / Lab Name", "Program Title", "Country / Timezone",
                    "Technical Domains", "Funding & Stipend", "Eligibility & Duration",
                    "Score", "Discovery Status", "Application Status", "Official Portal URL",
                    "Deadline", "Next Action"
                ]
                self._format_header_row(ws, headers)
                created_sheets.append("Institutional & National Labs")

            # 4. Unified Outreach Queue Sheet
            if "Unified Outreach Queue" not in wb.sheetnames:
                ws = wb.create_sheet(title="Unified Outreach Queue")
                headers = [
                    "Queue ID", "Category", "Organization", "Target Role / Lab",
                    "Contact Name", "Contact Email", "Timezone", "Score",
                    "Outreach Status", "Dispatched Timestamp", "Message-ID", "Delivery Status", "Next Action"
                ]
                self._format_header_row(ws, headers)
                created_sheets.append("Unified Outreach Queue")

            # 5. Application Tracker Sheet
            if "Application Tracker" not in wb.sheetnames:
                ws = wb.create_sheet(title="Application Tracker")
                headers = [
                    "App ID", "Category", "Organization", "Role / Program", "Channel",
                    "Application URL / Email", "Deadline", "Score", "Application Status",
                    "Submission Date", "Last Follow-Up", "Notes / Next Steps"
                ]
                self._format_header_row(ws, headers)
                created_sheets.append("Application Tracker")

            # 6. Unified Outreach Schedule Sheet
            if "Unified Outreach Schedule" not in wb.sheetnames:
                ws = wb.create_sheet(title="Unified Outreach Schedule")
                headers = [
                    "Schedule ID", "Priority Tier", "Category", "Organization", "Role Title",
                    "Channel", "Assigned Block", "Region", "IST Dispatch Window",
                    "Score", "Recipient / Portal", "Deadline", "Status", "Next Action"
                ]
                self._format_header_row(ws, headers)
                created_sheets.append("Unified Outreach Schedule")

            # Save atomically
            temp_path = self.workbook_path.with_suffix(f".tmp_sync_{os.getpid()}.xlsx")
            wb.save(str(temp_path))
            wb.close()
            shutil.move(str(temp_path), str(self.workbook_path))

            audit_logger.log_event(
                event_type="OPPORTUNITY_SHEETS_INITIALIZED",
                actor="OPPORTUNITY_WORKBOOK_SYNC",
                entity_type="WORKBOOK",
                entity_id=str(self.workbook_path),
                details={"created_sheets": created_sheets}
            )
            return {"status": "SUCCESS", "created_sheets": created_sheets}
        finally:
            self.release_lock()

    def sync_opportunities(
        self,
        opportunities: List[Opportunity],
        orgs_by_id: Dict[str, Organization]
    ) -> Dict[str, int]:
        """
        Synchronizes opportunities into their designated sheets based on opportunity_type.
        """
        self.acquire_lock()
        try:
            self.create_backup()
            wb = openpyxl.load_workbook(str(self.workbook_path))

            counts = {name: 0 for name in self.NEW_SHEET_NAMES}

            # Map sheets
            ws_startups = wb["Startups"] if "Startups" in wb.sheetnames else None
            ws_frontier = wb["Companies & Industrial R&D"] if "Companies & Industrial R&D" in wb.sheetnames else None
            ws_inst = wb["Institutional & National Labs"] if "Institutional & National Labs" in wb.sheetnames else None
            ws_queue = wb["Unified Outreach Queue"] if "Unified Outreach Queue" in wb.sheetnames else None
            ws_tracker = wb["Application Tracker"] if "Application Tracker" in wb.sheetnames else None

            # Sort opportunities by score descending
            sorted_opps = sorted(opportunities, key=lambda o: o.qualification_score, reverse=True)

            for opp in sorted_opps:
                org = orgs_by_id.get(opp.organization_id)
                org_name = org.name if org else opp.organization_id

                # 1. Startups
                if opp.opportunity_type == OpportunityType.STARTUP.value and ws_startups:
                    self._append_startup_row(ws_startups, opp, org)
                    counts["Startups"] += 1

                # 2. Companies & Industrial R&D
                elif opp.opportunity_type == OpportunityType.FRONTIER_COMPANY.value and ws_frontier:
                    self._append_frontier_row(ws_frontier, opp, org)
                    counts["Companies & Industrial R&D"] += 1

                # 3. Institutional & National Labs
                elif opp.opportunity_type == OpportunityType.INSTITUTIONAL_PROGRAM.value and ws_inst:
                    self._append_institutional_row(ws_inst, opp, org)
                    counts["Institutional & National Labs"] += 1

                # 4. Unified Outreach Queue (if verified email contact is present)
                if opp.contact_email and opp.contact_verification_status == "VERIFIED" and ws_queue:
                    self._append_outreach_queue_row(ws_queue, opp, org_name)
                    counts["Unified Outreach Queue"] += 1

                # 5. Application Tracker (all opportunities)
                if ws_tracker:
                    self._append_application_tracker_row(ws_tracker, opp, org_name)
                    counts["Application Tracker"] += 1

            # Save atomically
            temp_path = self.workbook_path.with_suffix(f".tmp_sync_{os.getpid()}.xlsx")
            wb.save(str(temp_path))
            wb.close()
            shutil.move(str(temp_path), str(self.workbook_path))

            return counts
        finally:
            self.release_lock()

    def sync_unified_schedule(self, schedule_records: List[Dict[str, Any]]) -> int:
        """
        Synchronizes records to the development Unified Outreach Schedule sheet.
        """
        self.acquire_lock()
        try:
            self.create_backup()
            wb = openpyxl.load_workbook(str(self.workbook_path))
            if "Unified Outreach Schedule" not in wb.sheetnames:
                ws = wb.create_sheet(title="Unified Outreach Schedule")
                headers = [
                    "Schedule ID", "Priority Tier", "Category", "Organization", "Role Title",
                    "Channel", "Assigned Block", "Region", "IST Dispatch Window",
                    "Score", "Recipient / Portal", "Deadline", "Status", "Next Action"
                ]
                self._format_header_row(ws, headers)
            else:
                ws = wb["Unified Outreach Schedule"]

            # Clear existing data rows (keep header row 1)
            if ws.max_row > 1:
                ws.delete_rows(2, ws.max_row - 1)

            count = 0
            for rec in schedule_records:
                row_idx = ws.max_row + 1
                values = [
                    rec.get("schedule_id", f"sched_{count+1}"),
                    rec.get("priority_tier", "PROMISING"),
                    rec.get("category", "STARTUP"),
                    rec.get("organization_name", ""),
                    rec.get("role_title", ""),
                    rec.get("channel", "EMAIL_DISPATCH"),
                    rec.get("assigned_block_id", "BLOCK_DOMESTIC_IN"),
                    rec.get("region_name", "India Domestic"),
                    rec.get("ist_window", "08:00 - 09:30 IST"),
                    rec.get("qualification_score", 0.0),
                    rec.get("contact_recipient", ""),
                    rec.get("deadline", "Rolling"),
                    rec.get("status", "QUEUED_FOR_REVIEW"),
                    rec.get("next_action", "")
                ]
                self._write_row(ws, row_idx, values)
                count += 1

            # Save atomically
            temp_path = self.workbook_path.with_suffix(f".tmp_sync_{os.getpid()}.xlsx")
            wb.save(str(temp_path))
            wb.close()
            shutil.move(str(temp_path), str(self.workbook_path))
            return count
        finally:
            self.release_lock()

    def _format_header_row(self, ws, headers: List[str]) -> None:
        ws.row_dimensions[1].height = 24
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(1, col_idx, value=h)
            cell.font = self.HEADER_FONT
            cell.fill = self.HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = self.BORDER
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = max(len(h) + 4, 16)

    def _append_startup_row(self, ws, opp: Opportunity, org: Optional[Organization]) -> None:
        row_idx = ws.max_row + 1
        rank = row_idx - 1
        values = [
            rank,
            org.name if org else opp.organization_id,
            org.funding_stage if org else "GROWTH",
            f"{opp.location or 'Global'} ({opp.iana_timezone})",
            opp.title,
            ", ".join(opp.technical_domains or []),
            opp.matched_project_id or "General ML",
            opp.compensation_details or opp.compensation_type,
            opp.qualification_score,
            opp.discovery_status,
            opp.application_status,
            opp.contact_name or "Hiring Team",
            opp.contact_email or "Portal",
            opp.application_url or (opp.official_evidence_urls[0] if opp.official_evidence_urls else ""),
            opp.next_action or "Review"
        ]
        self._write_row(ws, row_idx, values)

    def _append_frontier_row(self, ws, opp: Opportunity, org: Optional[Organization]) -> None:
        row_idx = ws.max_row + 1
        rank = row_idx - 1
        values = [
            rank,
            org.name if org else opp.organization_id,
            opp.title,
            f"{opp.location or 'Global'} ({opp.iana_timezone})",
            ", ".join(opp.technical_domains or []),
            opp.matched_project_id or "General ML",
            opp.compensation_details or opp.compensation_type,
            opp.eligibility_criteria or "Open",
            opp.qualification_score,
            opp.discovery_status,
            opp.application_status,
            opp.application_url or (opp.official_evidence_urls[0] if opp.official_evidence_urls else ""),
            opp.application_deadline or "Rolling",
            opp.next_action or "Apply"
        ]
        self._write_row(ws, row_idx, values)

    def _append_institutional_row(self, ws, opp: Opportunity, org: Optional[Organization]) -> None:
        row_idx = ws.max_row + 1
        rank = row_idx - 1
        values = [
            rank,
            org.name if org else opp.organization_id,
            opp.title,
            f"{opp.location or 'Global'} ({opp.iana_timezone})",
            ", ".join(opp.technical_domains or []),
            opp.compensation_details or opp.compensation_type,
            f"{opp.eligibility_criteria or 'Undergraduate'} ({opp.internship_duration_weeks} wks)",
            opp.qualification_score,
            opp.discovery_status,
            opp.application_status,
            opp.application_url or (opp.official_evidence_urls[0] if opp.official_evidence_urls else ""),
            opp.application_deadline or "Annual",
            opp.next_action or "Prepare Application"
        ]
        self._write_row(ws, row_idx, values)

    def _append_outreach_queue_row(self, ws, opp: Opportunity, org_name: str) -> None:
        row_idx = ws.max_row + 1
        values = [
            f"q_{opp.opportunity_id[:8]}",
            opp.opportunity_type,
            org_name,
            opp.title,
            opp.contact_name or "Contact",
            opp.contact_email,
            opp.iana_timezone,
            opp.qualification_score,
            "QUEUED_FOR_REVIEW",
            None,
            None,
            "UNSENT",
            opp.next_action or "Review Email"
        ]
        self._write_row(ws, row_idx, values)

    def _append_application_tracker_row(self, ws, opp: Opportunity, org_name: str) -> None:
        row_idx = ws.max_row + 1
        values = [
            f"app_{opp.opportunity_id[:8]}",
            opp.opportunity_type,
            org_name,
            opp.title,
            opp.hiring_contact_channel,
            opp.application_url or opp.contact_email or "",
            opp.application_deadline or "Rolling",
            opp.qualification_score,
            opp.application_status,
            None,
            None,
            opp.next_action or "Pending"
        ]
        self._write_row(ws, row_idx, values)

    def _write_row(self, ws, row_idx: int, values: List[Any]) -> None:
        ws.row_dimensions[row_idx].height = 20
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row_idx, col_idx, value=val)
            cell.font = self.DATA_FONT
            cell.border = self.BORDER
            if isinstance(val, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
