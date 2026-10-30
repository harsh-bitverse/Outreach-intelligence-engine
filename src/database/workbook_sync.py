"""
Authoritative Excel Workbook Synchronization Module.
Provides dynamic schema import, atomic updates, timestamped backups,
file locking, conflict detection, and Time Blocks synchronization.
"""

import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from config.settings import settings
from src.database.models import Professor, University
from src.database.repository import OutreachRepository, compute_durable_professor_id
from src.security.audit_logger import audit_logger


class WorkbookLockError(Exception):
    """Raised when the workbook lock cannot be acquired."""
    pass


class DynamicWorkbookSync:
    """Synchronizes between the authoritative Excel workbook and the SQLite database."""

    def __init__(self, workbook_path: Optional[Path] = None):
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.lock_file = self.workbook_path.with_suffix(".lock")

    def acquire_lock(self, timeout_seconds: int = 10) -> None:
        """Acquire an atomic file lock to prevent concurrent write corruption."""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            try:
                # O_CREAT | O_EXCL ensures atomic creation
                fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.close(fd)
                return
            except FileExistsError:
                time.sleep(0.1)
        raise WorkbookLockError(f"Could not acquire lock on {self.workbook_path} within {timeout_seconds}s.")

    def release_lock(self) -> None:
        """Release the file lock."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    def create_backup(self) -> Path:
        """Create a timestamped backup before any write operation."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"{self.workbook_path.stem}_pre_sync_{timestamp}{self.workbook_path.suffix}.bak"
        backup_path = self.workbook_path.parent / backup_name
        shutil.copy2(self.workbook_path, backup_path)
        audit_logger.log_event(
            event_type="WORKBOOK_BACKUP_CREATED",
            actor="SYSTEM_SYNC",
            entity_type="WORKBOOK",
            entity_id=str(self.workbook_path),
            details={"backup_path": str(backup_path)}
        )
        return backup_path

    # --------------------------------------------------------------------------
    # Dynamic Schema Import
    # --------------------------------------------------------------------------
    def import_to_database(self, repo: OutreachRepository) -> Dict[str, Any]:
        """
        Dynamically inspect and import the authoritative workbook into SQLite.
        Never discards rows, historical notes, or formulas.
        """
        if not self.workbook_path.exists():
            raise FileNotFoundError(f"Authoritative workbook not found at: {self.workbook_path}")

        wb = openpyxl.load_workbook(str(self.workbook_path), data_only=True)
        stats = {
            "professors_imported": 0,
            "universities_imported": 0,
            "suppressed_detected": 0,
            "sheets_found": wb.sheetnames
        }

        # 1. Import Universities if sheet present
        uni_sheet_name = next((s for s in wb.sheetnames if "university" in s.lower()), None)
        if uni_sheet_name:
            ws_uni = wb[uni_sheet_name]
            uni_stats = self._import_universities(ws_uni, repo)
            stats["universities_imported"] = uni_stats

        # 2. Import Professors
        tb_sheet_name = next(
            (s for s in wb.sheetnames if "time block" in s.lower() or "distribution" in s.lower()),
            None
        )
        if tb_sheet_name:
            ws_tb = wb[tb_sheet_name]
            prof_stats = self._import_professors_from_time_blocks(ws_tb, repo)
        else:
            prof_sheet_name = next((s for s in wb.sheetnames if "professor" in s.lower()), None)
            if not prof_sheet_name:
                raise ValueError(f"No professor or time blocks sheet found in workbook. Available: {wb.sheetnames}")
            ws_prof = wb[prof_sheet_name]
            prof_stats = self._import_professors(ws_prof, repo)

        stats["professors_imported"] = prof_stats["imported"]
        stats["suppressed_detected"] = prof_stats["suppressed"]

        repo.conn.commit()

        audit_logger.log_event(
            event_type="WORKBOOK_IMPORTED",
            actor="SYSTEM_SYNC",
            entity_type="WORKBOOK",
            entity_id=str(self.workbook_path),
            details=stats
        )
        return stats

    def _normalize_header(self, header: Optional[str]) -> str:
        """Normalize column header for robust dynamic matching."""
        if not header:
            return ""
        return "".join(c.lower() for c in str(header) if c.isalnum())

    def _import_professors(self, ws, repo: OutreachRepository) -> Dict[str, int]:
        # Read header row
        header_row = [cell.value for cell in ws[1]]
        header_map: Dict[str, int] = {}
        for idx, col in enumerate(header_row, start=1):
            norm = self._normalize_header(col)
            if norm:
                header_map[norm] = idx

        # Helper to get cell value by partial or normalized key
        def get_val(row, *aliases) -> str:
            for alias in aliases:
                norm_alias = self._normalize_header(alias)
                col_idx = header_map.get(norm_alias)
                if col_idx:
                    val = row[col_idx - 1]
                    return str(val).strip() if val is not None else ""
            return ""

        def get_int(row, *aliases) -> Optional[int]:
            for alias in aliases:
                norm_alias = self._normalize_header(alias)
                col_idx = header_map.get(norm_alias)
                if col_idx:
                    val = row[col_idx - 1]
                    try:
                        return int(val) if val is not None else None
                    except (ValueError, TypeError):
                        pass
            return None

        imported = 0
        suppressed_count = 0

        # Iterate all data rows
        for row in ws.iter_rows(min_row=2, values_only=True):
            full_name = get_val(row, "Professor Full Name", "Professor Name", "Name")
            uni_name = get_val(row, "University Name", "University", "Institution")

            # Skip empty rows
            if not full_name or not uni_name:
                continue

            email = get_val(row, "Official Institutional Email", "Email", "Email Address")
            country = get_val(row, "Country")
            title = get_val(row, "Academic Title / Position", "Title", "Position")
            dept = get_val(row, "Department / Faculty", "Department")
            qs_rank = get_int(row, "QS Rank", "Rank")
            seq = get_int(row, "Processing Sequence", "Sequence", "Seq")
            ver_status = get_val(row, "Email Verification Status", "Verification Status") or "UNVERIFIED"
            ver_url = get_val(row, "Email Verification Source URL", "Verification Source")
            profile_url = get_val(row, "Official Faculty Profile URL", "Profile URL")
            research_areas = get_val(row, "Research Areas / Keywords", "Research Areas")
            research_overlap = get_val(row, "Specific Research Overlap With My Interests", "Research Overlap")
            cv_skill = get_val(row, "Relevant Skill or Project From My CV", "CV Project")
            contribution = get_val(row, "Potential Software / Computational Contribution", "Contribution")
            evidence_url = get_val(row, "Research Evidence URL", "Evidence URL")
            date_ver = get_val(row, "Date Verified")
            notes = get_val(row, "Notes / Unresolved Issues", "Notes")

            # Existing outreach status if present in sheet
            sheet_outreach_status = get_val(row, "Outreach Status", "Mailing Status") or "NEW"

            prof_id = compute_durable_professor_id(uni_name, full_name)

            # Check suppression
            suppressed, reason = repo.is_suppressed(email)
            if suppressed:
                sheet_outreach_status = "SUPPRESSED"
                suppressed_count += 1
                if reason and reason not in notes:
                    notes = f"{notes} | {reason}".strip(" |")

            prof = Professor(
                professor_id=prof_id,
                qs_rank=qs_rank,
                processing_sequence=seq,
                university_name=uni_name,
                country=country,
                full_name=full_name,
                title=title,
                department=dept,
                email=email,
                email_verification_status=ver_status,
                verification_source_url=ver_url,
                faculty_profile_url=profile_url,
                research_areas=research_areas,
                research_overlap=research_overlap,
                cv_skill_project=cv_skill,
                potential_contribution=contribution,
                evidence_url=evidence_url,
                date_verified=date_ver,
                notes=notes,
                outreach_status=sheet_outreach_status
            )
            repo.upsert_professor(prof)
            imported += 1

        return {"imported": imported, "suppressed": suppressed_count}

    def _import_professors_from_time_blocks(self, ws, repo: OutreachRepository) -> Dict[str, int]:
        """
        Dynamically import professors directly from the authoritative 'Time Blocks & Distribution' sheet.
        Preserves:
        1. Exact professor sequence across blocks.
        2. Block association (BLOCK 01 to BLOCK 13), region, and target windows.
        3. Research overlap, verified project links, and computational contributions.
        4. University IANA Time Zones.
        """
        header_map: Dict[str, int] = {}
        current_block_id = "BLOCK_01"
        current_block_label = ""
        current_region = ""
        current_ist_window = ""

        imported = 0
        suppressed_count = 0
        order_index = 0

        def get_val_by_map(row, header_dict, *aliases) -> str:
            for alias in aliases:
                norm_alias = self._normalize_header(alias)
                col_idx = header_dict.get(norm_alias)
                if col_idx is not None and col_idx - 1 < len(row):
                    val = row[col_idx - 1]
                    return str(val).strip() if val is not None else ""
            return ""

        def get_int_by_map(row, header_dict, *aliases) -> Optional[int]:
            for alias in aliases:
                norm_alias = self._normalize_header(alias)
                col_idx = header_dict.get(norm_alias)
                if col_idx is not None and col_idx - 1 < len(row):
                    val = row[col_idx - 1]
                    try:
                        return int(val) if val is not None else None
                    except (ValueError, TypeError):
                        pass
            return None

        for row in ws.iter_rows(values_only=True):
            if not row or not any(cell is not None for cell in row):
                continue

            first_cell = str(row[0] or "").strip()

            # Detect block banner line
            # e.g., "BLOCK 01 / 13 | 01:00–02:00 IST | 1 university • 7 professor rows | Target: 09:00 Local (NZDT UTC+13) | Region: New Zealand"
            if first_cell.upper().startswith("BLOCK "):
                current_block_label = first_cell
                parts = [p.strip() for p in first_cell.split("|")]
                if len(parts) >= 1:
                    match_block = re.search(r"BLOCK\s*(\d+)", parts[0], re.IGNORECASE)
                    if match_block:
                        current_block_id = f"BLOCK_{int(match_block.group(1)):02d}"
                if len(parts) >= 2:
                    current_ist_window = parts[1]
                if len(parts) >= 5:
                    current_region = parts[4].replace("Region:", "").strip()
                continue

            # Detect table headers under each block
            if self._normalize_header(first_cell) in ("qsrank", "rank", "processingsequence"):
                header_map = {}
                for idx, col in enumerate(row, start=1):
                    norm = self._normalize_header(col)
                    if norm:
                        header_map[norm] = idx
                continue

            # If headers are not yet established, skip promotional/notice rows
            if not header_map:
                continue

            full_name = get_val_by_map(row, header_map, "Professor Full Name", "Professor Name", "Name")
            uni_name = get_val_by_map(row, header_map, "University Name", "University", "Institution")

            # Skip rows without name or university
            if not full_name or not uni_name:
                continue

            # Check if this row is actually a header row
            if self._normalize_header(full_name) in ("professorfullname", "fullname"):
                continue

            order_index += 1

            email = get_val_by_map(row, header_map, "Official Institutional Email", "Email", "Email Address")
            country = get_val_by_map(row, header_map, "Country")
            title = get_val_by_map(row, header_map, "Academic Title / Position", "Title", "Position")
            dept = get_val_by_map(row, header_map, "Department / Faculty", "Department")
            qs_rank = get_int_by_map(row, header_map, "QS Rank", "Rank")
            seq = get_int_by_map(row, header_map, "Processing Sequence", "Sequence", "Seq")
            ver_status = get_val_by_map(row, header_map, "Email Verification Status", "Verification Status") or "UNVERIFIED"
            ver_url = get_val_by_map(row, header_map, "Email Verification Source URL", "Verification Source")
            profile_url = get_val_by_map(row, header_map, "Official Faculty Profile URL", "Profile URL")
            research_areas = get_val_by_map(row, header_map, "Research Areas / Keywords", "Research Areas")
            research_overlap = get_val_by_map(row, header_map, "Specific Research Overlap With My Interests", "Research Overlap")
            cv_skill = get_val_by_map(row, header_map, "Relevant Skill or Project From My CV", "CV Project")
            contribution = get_val_by_map(row, header_map, "Potential Software / Computational Contribution", "Contribution")
            evidence_url = get_val_by_map(row, header_map, "Research Evidence URL", "Evidence URL")
            date_ver = get_val_by_map(row, header_map, "Date Verified")
            notes = get_val_by_map(row, header_map, "Notes / Unresolved Issues", "Notes")
            iana_tz = get_val_by_map(row, header_map, "University IANA Time Zone", "Timezone", "IANA Time Zone")
            sheet_outreach_status = get_val_by_map(row, header_map, "Outreach Status", "Mailing Status") or "NEW"

            prof_id = compute_durable_professor_id(uni_name, full_name)

            # Check suppression
            suppressed, reason = repo.is_suppressed(email)
            if suppressed:
                sheet_outreach_status = "SUPPRESSED"
                suppressed_count += 1
                if reason and reason not in notes:
                    notes = f"{notes} | {reason}".strip(" |")

            prof = Professor(
                professor_id=prof_id,
                qs_rank=qs_rank,
                processing_sequence=seq,
                university_name=uni_name,
                country=country,
                full_name=full_name,
                title=title,
                department=dept,
                email=email,
                email_verification_status=ver_status,
                verification_source_url=ver_url,
                faculty_profile_url=profile_url,
                research_areas=research_areas,
                research_overlap=research_overlap,
                cv_skill_project=cv_skill,
                potential_contribution=contribution,
                evidence_url=evidence_url,
                date_verified=date_ver,
                notes=notes,
                outreach_status=sheet_outreach_status,
                time_block=current_block_id,
                time_block_order=order_index,
                iana_timezone=iana_tz
            )
            repo.upsert_professor(prof)
            imported += 1

        return {"imported": imported, "suppressed": suppressed_count}

    def _import_universities(self, ws, repo: OutreachRepository) -> int:
        header_row = [cell.value for cell in ws[1]]
        header_map: Dict[str, int] = {}
        for idx, col in enumerate(header_row, start=1):
            norm = self._normalize_header(col)
            if norm:
                header_map[norm] = idx

        def get_val(row, *aliases) -> str:
            for alias in aliases:
                col_idx = header_map.get(self._normalize_header(alias))
                if col_idx:
                    val = row[col_idx - 1]
                    return str(val).strip() if val is not None else ""
            return ""

        def get_int(row, *aliases) -> int:
            for alias in aliases:
                col_idx = header_map.get(self._normalize_header(alias))
                if col_idx:
                    val = row[col_idx - 1]
                    try:
                        return int(val) if val is not None else 0
                    except (ValueError, TypeError):
                        pass
            return 0

        count = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            uni_name = get_val(row, "University Name", "University")
            if not uni_name:
                continue
            country = get_val(row, "Country")
            qs_rank = get_int(row, "QS Rank")
            seq = get_int(row, "Processing Sequence")
            city = get_val(row, "City / Campus", "City")
            official_url = get_val(row, "Official University URL", "Official URL")
            review_status = get_val(row, "University Review Status", "Review Status")
            depts = get_val(row, "Departments / Faculties Reviewed", "Departments")
            dirs = get_val(row, "Faculty Directories Reviewed", "Directories")
            screened = get_int(row, "Faculty Profiles Screened")
            shortlisted = get_int(row, "Professors Shortlisted")
            ver_emails = get_int(row, "Verified Professor Emails")
            unver_emails = get_int(row, "Unverified Professor Emails")

            uni = University(
                university_name=uni_name,
                country=country,
                qs_rank=qs_rank if qs_rank > 0 else None,
                processing_sequence=seq if seq > 0 else None,
                city=city,
                official_url=official_url,
                review_status=review_status or "UNREVIEWED",
                departments_reviewed=depts,
                directories_reviewed=dirs,
                profiles_screened=screened,
                professors_shortlisted=shortlisted,
                verified_emails=ver_emails,
                unverified_emails=unver_emails,
                timezone_iana="UTC"
            )
            repo.upsert_university(uni)
            count += 1

        return count

    # --------------------------------------------------------------------------
    # Atomic Sync Write Back to Workbook
    # --------------------------------------------------------------------------
    def sync_back_to_workbook(self, repo: OutreachRepository, time_blocks_data: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Write updated statuses back to the workbook non-destructively:
        1. Lock workbook.
        2. Create timestamped backup.
        3. Load existing workbook with openpyxl (preserving formulas and sheets).
        4. Update or add 'Outreach Status' and 'Message Thread ID' in 'Professor Database'.
        5. Synchronize or create 'Time Blocks and Distribution' sheet.
        6. Save to a temporary file and atomically rename.
        7. Release lock.
        """
        self.acquire_lock()
        try:
            backup_path = self.create_backup()

            wb = openpyxl.load_workbook(str(self.workbook_path))

            # 1. Update Professor Database sheet
            prof_sheet_name = next((s for s in wb.sheetnames if "professor" in s.lower()), None)
            if prof_sheet_name:
                ws_prof = wb[prof_sheet_name]
                self._update_professor_sheet(ws_prof, repo)

            # 2. Update or Create Time Blocks and Distribution sheet
            if time_blocks_data:
                self._sync_time_blocks_sheet(wb, time_blocks_data)

            # 3. Write to temporary file
            temp_path = self.workbook_path.with_suffix(f".tmp_{os.getpid()}.xlsx")
            wb.save(str(temp_path))
            wb.close()

            # 4. Atomic replace
            shutil.move(str(temp_path), str(self.workbook_path))

            audit_logger.log_event(
                event_type="WORKBOOK_SYNCED_BACK",
                actor="SYSTEM_SYNC",
                entity_type="WORKBOOK",
                entity_id=str(self.workbook_path),
                details={"backup_created": str(backup_path)}
            )
        finally:
            self.release_lock()

    def update_time_blocks_row(
        self,
        email: str,
        status: str = "SENT",
        sent_at: Optional[str] = None,
        message_id: Optional[str] = None,
        timeout_seconds: int = 5
    ) -> bool:
        """
        Update the corresponding professor row in the SAME 'Time Blocks & Distribution' worksheet.
        Preserves existing university information, research notes, ordering, formatting, and formulas.
        Updates:
          - Column 25 (Outreach Eligibility) -> status
          - Column 29 (Outreach Status) -> status
          - Column 30 (Sent Timestamp) -> sent_at (ISO formatted)
          - Column 31 (Message-ID) -> message_id
        Also updates the corresponding row in 'Professor Database' if present.
        Non-fatal if Excel is locked: sent history remains preserved in SQLite.
        """
        if not email:
            return False

        target_email = email.strip().lower()
        now_iso = sent_at or datetime.now(timezone.utc).isoformat()
        msg_id = message_id or ""

        try:
            self.acquire_lock(timeout_seconds=timeout_seconds)
        except WorkbookLockError as e:
            audit_logger.log_event(
                event_type="WORKBOOK_UPDATE_LOCKED",
                actor="SYSTEM_SCHEDULER",
                entity_type="WORKBOOK",
                entity_id=str(self.workbook_path),
                details={"email": email, "status": status, "warning": str(e)}
            )
            return False

        try:
            backup_path = self.create_backup()
            wb = openpyxl.load_workbook(str(self.workbook_path))

            # 1. Update in 'Time Blocks & Distribution'
            tb_sheet_name = next(
                (s for s in wb.sheetnames if "time blocks" in s.lower()),
                None
            )
            updated_tb = False
            if tb_sheet_name:
                ws = wb[tb_sheet_name]
                email_col_idx = 8  # Default Column 8 is 'Official Institutional Email'

                for r_idx in range(1, ws.max_row + 1):
                    first_cell = str(ws.cell(r_idx, 1).value or "").strip()
                    # Check if this row is a table header row
                    if self._normalize_header(first_cell) in ("qsrank", "rank", "processingsequence"):
                        # Locate exact column index for email if present
                        for c_idx in range(1, ws.max_column + 1):
                            norm = self._normalize_header(ws.cell(r_idx, c_idx).value)
                            if norm in ("officialinstitutionalemail", "email", "emailaddress"):
                                email_col_idx = c_idx

                        # Ensure columns 29, 30, 31 have headers in this block's header row
                        if not ws.cell(r_idx, 29).value:
                            ws.cell(r_idx, 29).value = "Outreach Status"
                            ws.cell(r_idx, 29).font = Font(name="Calibri", size=10, bold=True)
                        if not ws.cell(r_idx, 30).value:
                            ws.cell(r_idx, 30).value = "Sent Timestamp"
                            ws.cell(r_idx, 30).font = Font(name="Calibri", size=10, bold=True)
                        if not ws.cell(r_idx, 31).value:
                            ws.cell(r_idx, 31).value = "Message-ID"
                            ws.cell(r_idx, 31).font = Font(name="Calibri", size=10, bold=True)
                        continue

                    # Check if row matches target email
                    cell_email = ws.cell(r_idx, email_col_idx).value
                    if cell_email and str(cell_email).strip().lower() == target_email:
                        # Col 25: Outreach Eligibility
                        ws.cell(r_idx, 25).value = status
                        # Col 29: Outreach Status
                        ws.cell(r_idx, 29).value = status
                        # Col 30: Sent Timestamp
                        ws.cell(r_idx, 30).value = now_iso
                        # Col 31: Message-ID
                        ws.cell(r_idx, 31).value = msg_id
                        updated_tb = True
                        break

            # 2. Also update 'Professor Database' sheet if present
            prof_sheet_name = next((s for s in wb.sheetnames if "professor" in s.lower()), None)
            if prof_sheet_name:
                ws_p = wb[prof_sheet_name]
                p_email_col = 8
                p_status_col = None
                p_headers = [ws_p.cell(1, c).value for c in range(1, ws_p.max_column + 1)]
                for c_idx, h in enumerate(p_headers, start=1):
                    norm = self._normalize_header(h)
                    if norm in ("officialinstitutionalemail", "email"):
                        p_email_col = c_idx
                    elif norm in ("outreachstatus", "status"):
                        p_status_col = c_idx

                if not p_status_col:
                    p_status_col = len(p_headers) + 1
                    ws_p.cell(1, p_status_col).value = "Outreach Status"
                    ws_p.cell(1, p_status_col).font = Font(bold=True)

                for r_idx in range(2, ws_p.max_row + 1):
                    val = ws_p.cell(r_idx, p_email_col).value
                    if val and str(val).strip().lower() == target_email:
                        ws_p.cell(r_idx, p_status_col).value = status
                        break

            # 3. Save atomically via temp file
            temp_path = self.workbook_path.with_suffix(f".tmp_{os.getpid()}_{int(time.time()*1000)}.xlsx")
            wb.save(str(temp_path))
            wb.close()
            shutil.move(str(temp_path), str(self.workbook_path))

            audit_logger.log_event(
                event_type="WORKBOOK_ROW_UPDATED",
                actor="SYSTEM_SCHEDULER",
                entity_type="WORKBOOK_ROW",
                entity_id=email,
                details={
                    "email": email,
                    "status": status,
                    "sent_at": now_iso,
                    "message_id": msg_id,
                    "sheet_updated": tb_sheet_name,
                    "found": updated_tb
                }
            )
            return True
        except Exception as err:
            audit_logger.log_event(
                event_type="WORKBOOK_ROW_UPDATE_ERROR",
                actor="SYSTEM_SCHEDULER",
                entity_type="WORKBOOK_ROW",
                entity_id=email,
                details={"error": str(err)}
            )
            return False
        finally:
            self.release_lock()

    def _update_professor_sheet(self, ws, repo: OutreachRepository) -> None:
        """Update outreach columns in the existing Professor sheet."""
        headers = [cell.value for cell in ws[1]]
        header_norms = {self._normalize_header(h): idx for idx, h in enumerate(headers, start=1)}

        # Find or create 'Outreach Status' column
        status_col = header_norms.get("outreachstatus")
        if not status_col:
            status_col = len(headers) + 1
            cell = ws.cell(row=1, column=status_col)
            cell.value = "Outreach Status"
            cell.font = Font(bold=True)

        uni_col = header_norms.get("universityname") or header_norms.get("university") or 3
        name_col = header_norms.get("professorfullname") or header_norms.get("professorname") or 5

        for row_idx in range(2, ws.max_row + 1):
            uni_val = ws.cell(row=row_idx, column=uni_col).value
            name_val = ws.cell(row=row_idx, column=name_col).value
            if not uni_val or not name_val:
                continue

            prof_id = compute_durable_professor_id(str(uni_val), str(name_val))
            prof_data = repo.get_professor(prof_id)
            if prof_data:
                current_status = prof_data["outreach_status"]
                ws.cell(row=row_idx, column=status_col).value = current_status

    def _sync_time_blocks_sheet(self, wb, time_blocks_data: List[Dict[str, Any]]) -> None:
        """Create or update the 'Time Blocks and Distribution' sheet."""
        sheet_name = "Time Blocks and Distribution"
        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            # Clear existing data rows but keep headers
            ws.delete_rows(2, ws.max_row)
        else:
            ws = wb.create_sheet(title=sheet_name)

        # Write Headers
        headers = [
            "Block ID", "Region / Cluster", "Reference IST Dispatch Time",
            "Target Destination Local Time", "Country Priorities",
            "Total Universities", "Shortlisted Faculty Count", "Status / Notes"
        ]
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Write Block Rows
        for block in time_blocks_data:
            ws.append([
                block.get("block_id", ""),
                block.get("region", ""),
                block.get("ist_time", ""),
                block.get("local_time", "8:00 AM - 9:00 AM"),
                block.get("countries", ""),
                block.get("university_count", 0),
                block.get("faculty_count", 0),
                block.get("notes", "Scheduled for weekday morning arrival")
            ])

        # Adjust column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 14)
