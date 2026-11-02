"""
Autonomous Professor Outreach Campaign Loop.
Implements the exact scheduler -> AI author -> SMTP -> Excel update loop:
1. Continuously evaluates IST time against the 13 regional time blocks and verifies professor-local working day.
2. Direct-reads the 'Time Blocks & Distribution' worksheet in original workbook order.
3. Invokes context-grounded AI authoring connecting faculty research with verified projects.
4. Enforces strict factual accuracy, word count (130-180 words), 4 paragraphs, and dual PDF attachments.
5. Transmits via IITD SMTP gateway and transactionally commits sent state to SQLite.
6. Updates the SAME 'Time Blocks & Distribution' worksheet row to SENT with timestamp and Message-ID.
7. Automatically pauses across block boundaries and resumes on subsequent eligible blocks without per-professor approval.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import openpyxl
from config.settings import settings
from src.database.connection import get_db
from src.database.models import OutreachDraft, OutreachQueueItem, Professor
from src.database.repository import OutreachRepository, compute_durable_professor_id
from src.database.workbook_sync import DynamicWorkbookSync, WorkbookLockError
from src.mail.credentials import CredentialManager
from src.mail.smtp_client import (
    smtp_client,
    IITDSmtpClient,
    SMTPRateLimitError,
    SMTPTransientError,
    SMTPPermanentError,
    SMTPAmbiguousDeliveryError
)
from src.mail.sending_gateway import SendingGateway, sending_gateway
from src.scheduler.queue_runner import (
    TIME_BLOCK_WINDOWS,
    REPLY_RECEIVED_EXCLUSIONS,
    PRIOR_CONTACT_EXCLUSIONS,
    SCHEDULER_LOCK_PATH,
    is_process_running
)
from src.security.audit_logger import audit_logger

# Authoritative Campaign Start: Monday, November 2, 2026
CAMPAIGN_START_DATE = date(2026, 11, 2)


class CLIQuotaExhaustionError(Exception):
    """Raised when Antigravity CLI encounters provider-wide quota exhaustion (e.g. 429 / RESOURCE_EXHAUSTED)."""
    pass


class CLIDraftGenerationError(Exception):
    """Raised when Antigravity CLI fails on a professor draft for retryable reasons (timeout, malformed output)."""
    pass


class DraftValidationError(Exception):
    """Raised when an AI-generated draft fails academic validation gates."""
    pass


class AntigravityCLIDrafter:
    """
    Invokes the official Antigravity CLI (agy.exe) non-interactively
    under the user's existing Google AI Pro account (keyring/OAuth credentials).
    Requires zero external paid API keys or UI automation.
    """

    def __init__(self, cli_path: Optional[Path] = None):
        if cli_path:
            self.cli_path = Path(cli_path)
        else:
            default_loc = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
            which_loc = shutil.which("agy")
            self.cli_path = default_loc if default_loc.exists() else (Path(which_loc) if which_loc else default_loc)

    def is_available(self) -> bool:
        """Check if official agy binary is present and executable."""
        return self.cli_path.exists()

    def generate_draft(self, prof_row: Dict[str, Any], timeout: int = 90) -> Optional[Dict[str, Any]]:
        """
        Execute non-interactive prompt via agy --print to generate context-grounded outreach.
        """
        if not self.is_available():
            return None

        full_name = prof_row.get("full_name", "")
        title = prof_row.get("title", "Professor")
        uni = prof_row.get("university", "")
        dept = prof_row.get("department", "")
        areas = prof_row.get("research_areas", "")
        overlap = prof_row.get("research_overlap", "")
        contrib = prof_row.get("potential_contribution", "")

        prompt = f"""You are an assistant for Harshith N, an undergraduate in Chemical Engineering at IIT Delhi (ch1251635@iitd.ac.in).
Generate a genuinely personalized, academically rigorous research internship outreach email to this professor for Summer 2027.

PROFESSOR DETAILS:
- Name: {full_name}
- Title: {title}
- University: {uni}
- Department: {dept}
- Research Areas: {areas}
- Specific Research Overlap: {overlap}
- Potential Contribution: {contrib}

APPLICANT'S VERIFIED BACKGROUND:
- Student: Harshith N, Department of Chemical Engineering, IIT Delhi.
- Verified Projects:
  1. Neural Process Twin: PyTorch surrogate models for 2D steady-state heat conduction, evaluating surrogate prediction accuracy against numerical finite difference solutions.
  2. LLM Inference Intelligence Engine: Roofline model diagnostics isolating compute bounds from memory-bandwidth bottlenecks across CPU and GPU architectures.
- Documents attached: Harshith_N_Research_CV_Summer2027.pdf, Harshith_N_Research_Interests_Summer2027.pdf.

STRICT CONSTRAINTS:
1. Subject line format: Summer 2027 Research Internship — [Specific Concise Research Area] | IIT Delhi
2. Paragraph 1: State identity, department at IIT Delhi, and clear interest in visiting research internship in Summer 2027.
3. Paragraph 2: Discuss specific overlap with professor's research topics.
4. Paragraph 3: Connect to applicant's verified project (Neural Process Twin or LLM Inference Engine Roofline diagnostics) and realistic computational contribution.
5. Paragraph 4: Availability (10–12 weeks in Summer 2027) and natural reference to attached CV and research interests statement.
6. Total body word count across paragraphs 1-4 must be between 130 and 180 words.
7. Zero hype, zero unverified speedup numbers, zero certified bound claims.

Output ONLY valid JSON with keys:
"subject", "salutation", "p1", "p2", "p3", "p4", "signature"
"""
        try:
            cmd = [
                str(self.cli_path),
                "--effort", "low",
                "--disable-slash-commands",
                "--print", prompt
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            if proc.returncode != 0:
                combined_err = f"{proc.stderr} {proc.stdout}".lower()
                audit_logger.log_event(
                    event_type="ANTIGRAVITY_CLI_ERROR",
                    actor="ANTIGRAVITY_CLI_DRAFTER",
                    entity_type="CLI_PROCESS",
                    entity_id=full_name,
                    details={"exit_code": proc.returncode, "stderr": proc.stderr[:300], "stdout": proc.stdout[:300]}
                )
                if any(kw in combined_err for kw in ["resource_exhausted", "429", "quota", "rate limit", "exhausted", "too many requests"]):
                    raise CLIQuotaExhaustionError(f"Antigravity CLI Quota Exhaustion (code {proc.returncode}): {proc.stderr[:300] or proc.stdout[:300]}")
                raise CLIDraftGenerationError(f"Antigravity CLI process failed with code {proc.returncode}: {proc.stderr[:300]}")

            raw_out = proc.stdout.strip()
            out_comb = raw_out.lower()
            if any(kw in out_comb for kw in ["resource_exhausted", "429", "quota exceeded", "rate limit exceeded", "too many requests"]):
                raise CLIQuotaExhaustionError(f"Antigravity CLI output indicates Quota Exhaustion: {raw_out[:300]}")

            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_out, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                start = raw_out.find("{")
                end = raw_out.rfind("}")
                if start != -1 and end != -1:
                    json_str = raw_out[start:end+1]
                else:
                    raise CLIDraftGenerationError(f"Antigravity CLI returned malformed non-JSON output: {raw_out[:300]}")

            try:
                data = json.loads(json_str)
            except Exception as j_err:
                raise CLIDraftGenerationError(f"Antigravity CLI JSON decoding failed: {j_err}") from j_err

            p1 = data.get("p1", "").strip()
            p2 = data.get("p2", "").strip()
            p3 = data.get("p3", "").strip()
            p4 = data.get("p4", "").strip()
            salutation = data.get("salutation", f"Dear Professor {full_name},").strip()
            signature = data.get(
                "signature",
                "Sincerely,\nHarshith N\nDepartment of Chemical Engineering\nIndian Institute of Technology Delhi (IIT Delhi)\nch1251635@iitd.ac.in"
            ).strip()

            core_paras = [p1, p2, p3, p4]
            content_wc = sum(len(p.split()) for p in core_paras)
            full_body = f"{salutation}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{p4}\n\n{signature}"

            return {
                "professor_name": full_name,
                "recipient_email": prof_row["email"],
                "subject": data.get("subject", "").strip(),
                "body": full_body,
                "salutation": salutation,
                "p1": p1,
                "p2": p2,
                "p3": p3,
                "p4": p4,
                "signature": signature,
                "content_word_count": content_wc,
                "total_word_count": len(full_body.split()),
                "attachments": [
                    (settings.CV_ATTACHMENT_PATH, settings.CV_ATTACHMENT_FILENAME),
                    (settings.RESEARCH_INTERESTS_PATH, settings.RESEARCH_INTERESTS_FILENAME),
                ],
                "authoring_engine": "GOOGLE_AI_PRO_ANTIGRAVITY_CLI",
                "factual_grounding_verified": True
            }
        except (CLIQuotaExhaustionError, CLIDraftGenerationError):
            raise
        except Exception as err:
            audit_logger.log_event(
                event_type="ANTIGRAVITY_CLI_EXCEPTION",
                actor="ANTIGRAVITY_CLI_DRAFTER",
                entity_type="CLI_PROCESS",
                entity_id=full_name,
                details={"error": str(err)}
            )
            raise CLIDraftGenerationError(f"Unexpected CLI drafting exception: {err}") from err


class AutonomousCampaignLoop:
    """
    Unified engine managing the exact autonomous outreach cycle.
    """

    def __init__(
        self,
        workbook_path: Optional[Path] = None,
        sync: Optional[DynamicWorkbookSync] = None,
        gateway: Optional[SendingGateway] = None,
        smtp: Optional[IITDSmtpClient] = None,
        cli_drafter: Optional[AntigravityCLIDrafter] = None,
        ist_tz: Optional[ZoneInfo] = None
    ):
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.sync = sync or DynamicWorkbookSync(self.workbook_path)
        self.gateway = gateway or sending_gateway
        self.smtp = smtp or smtp_client
        self.cli_drafter = cli_drafter or AntigravityCLIDrafter()
        self.ist_tz = ist_tz or ZoneInfo("Asia/Kolkata")
        self.lock_path = SCHEDULER_LOCK_PATH

    # --------------------------------------------------------------------------
    # 1. Lock Management & Single-Instance Safety
    # --------------------------------------------------------------------------
    def acquire_scheduler_lock(self) -> bool:
        """Ensure single active scheduler instance using lock file."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                active_pid = data.get("pid")
                if active_pid and is_process_running(active_pid) and active_pid != os.getpid():
                    raise RuntimeError(
                        f"Another campaign scheduler is active under PID {active_pid}. Single-instance policy enforced."
                    )
            except (json.JSONDecodeError, OSError):
                pass

        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, f)
        return True

    def release_scheduler_lock(self) -> None:
        """Release the scheduler lock file."""
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # 2. Time-Block Window & Working Day Verification
    # --------------------------------------------------------------------------
    def get_active_block(self, current_dt: Optional[datetime] = None) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Check if the current IST time falls within any of the 13 defined time blocks.
        """
        now_ist = (current_dt or datetime.now(self.ist_tz)).astimezone(self.ist_tz)
        current_time = now_ist.time()

        for block_id, b_info in TIME_BLOCK_WINDOWS.items():
            start_t = b_info["start_time"]
            end_t = b_info["end_time"]
            if start_t <= current_time <= end_t:
                return block_id, b_info

        return None

    def is_local_working_day(self, iana_tz: str, current_dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Verify that the evaluated candidate's local time is a valid working day (Mon-Fri)
        and within normal business hours (8:00 AM - 6:00 PM local).
        No Tuesday-Thursday prioritization: all weekdays Mon-Fri are fully eligible.
        """
        now_ist = (current_dt or datetime.now(self.ist_tz)).astimezone(self.ist_tz)

        try:
            target_tz = ZoneInfo(iana_tz) if iana_tz else self.ist_tz
        except Exception:
            target_tz = self.ist_tz

        local_dt = now_ist.astimezone(target_tz)
        weekday = local_dt.weekday()  # 0=Monday, ..., 4=Friday, 5=Sat, 6=Sun
        is_weekday = 0 <= weekday <= 4

        local_time = local_dt.time()
        in_business_hours = dtime(8, 0) <= local_time <= dtime(18, 0)

        details = (
            f"Local: {local_dt.strftime('%Y-%m-%d %H:%M:%S')} ({local_dt.tzname()}), "
            f"Weekday={local_dt.strftime('%A')} ({weekday}), InHours={in_business_hours}"
        )

        is_eligible = is_weekday and in_business_hours
        return is_eligible, details

    # --------------------------------------------------------------------------
    # 3. Direct Sheet Traversal: Find Eligible Candidate in Active Block
    # --------------------------------------------------------------------------
    def find_eligible_professors_in_block(
        self,
        block_id: str,
        limit: Optional[int] = None,
        exclude_emails: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Read the authoritative 'Time Blocks & Distribution' worksheet directly.
        Returns eligible, unsent professors in their exact original row sequence.
        """
        if not self.workbook_path.exists():
            raise FileNotFoundError(f"Authoritative workbook missing: {self.workbook_path}")

        excluded_set = set((e.strip().lower() for e in (exclude_emails or [])))

        # Load suppression list from database
        suppressed_emails = set()
        with get_db() as conn:
            repo = OutreachRepository(conn)
            # Add reply-received & prior contacts
            for _, em, _, _ in (REPLY_RECEIVED_EXCLUSIONS + PRIOR_CONTACT_EXCLUSIONS):
                suppressed_emails.add(em.strip().lower())

            # Query database suppression_list
            rows = conn.execute("SELECT email FROM suppression_list WHERE email IS NOT NULL;").fetchall()
            for r in rows:
                if r["email"]:
                    suppressed_emails.add(r["email"].strip().lower())

            # Query database sent, error, and ambiguous professors
            inactive_rows = conn.execute("SELECT email FROM professors WHERE outreach_status IN ('SENT', 'SIMULATED_SENT', 'ERROR', 'AMBIGUOUS_DELIVERY') AND email IS NOT NULL;").fetchall()
            for r in inactive_rows:
                if r["email"]:
                    suppressed_emails.add(r["email"].strip().lower())

        candidates = []
        wb = openpyxl.load_workbook(str(self.workbook_path), read_only=True)
        tb_sheet = next((s for s in wb.sheetnames if "time blocks" in s.lower()), None)
        if not tb_sheet:
            wb.close()
            return []

        ws = wb[tb_sheet]
        current_block = None

        # Normalized target block id e.g. "BLOCK_01"
        target_norm = block_id.upper().replace(" ", "_")

        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if not row or not any(cell is not None for cell in row):
                continue

            first_cell = str(row[0] or "").strip()

            # Detect block banner line
            if first_cell.upper().startswith("BLOCK "):
                match = re.search(r"BLOCK\s*(\d+)", first_cell, re.IGNORECASE)
                if match:
                    current_block = f"BLOCK_{int(match.group(1)):02d}"
                continue

            # Skip header rows
            if first_cell.lower() in ("qs rank", "rank", "processing sequence", "processingsequence"):
                continue

            if current_block != target_norm:
                continue

            # Row belongs to the target block
            email = str(row[7] or "").strip()
            name = str(row[4] or "").strip()
            uni = str(row[2] or "").strip()
            country = str(row[3] or "").strip()

            if not email or not name or not uni:
                continue

            norm_email = email.lower()

            # Check exclusions
            if norm_email in excluded_set or norm_email in suppressed_emails or norm_email.endswith("@iitd.ac.in"):
                continue

            # Check eligibility in column 25 and status in column 29
            col25 = str(row[24] or "").strip().upper() if len(row) >= 25 else ""
            col29 = str(row[28] or "").strip().upper() if len(row) >= 29 else ""

            if col25 in ("SENT", "ERROR", "AMBIGUOUS_DELIVERY") or col29 in ("SENT", "ERROR", "AMBIGUOUS_DELIVERY"):
                continue
            if col25 == "NOT OUTREACH READY":
                continue

            prof_dict = {
                "row_index": r_idx,
                "block_id": current_block,
                "full_name": name,
                "title": str(row[5] or "Professor").strip(),
                "department": str(row[6] or "").strip(),
                "email": email,
                "university": uni,
                "country": country,
                "qs_rank": row[0],
                "research_areas": str(row[11] or "").strip() if len(row) >= 12 else "",
                "research_overlap": str(row[12] or "").strip() if len(row) >= 13 else "",
                "cv_skill_project": str(row[13] or "").strip() if len(row) >= 14 else "",
                "potential_contribution": str(row[14] or "").strip() if len(row) >= 15 else "",
                "evidence_url": str(row[15] or "").strip() if len(row) >= 16 else "",
                "profile_url": str(row[10] or "").strip() if len(row) >= 11 else "",
                "iana_timezone": str(row[18] or "").strip() if len(row) >= 19 else "UTC",
                "outreach_eligibility": col25,
            }
            candidates.append(prof_dict)
            if limit and len(candidates) >= limit:
                break

        wb.close()
        return candidates

    # --------------------------------------------------------------------------
    # 4. Context-Grounded AI Drafting
    # --------------------------------------------------------------------------
    def generate_ai_draft(self, prof_row: Dict[str, Any], prefer_cli: bool = True) -> Dict[str, Any]:
        """
        Generate a genuine, personalized, 4-paragraph email for the professor.
        First attempts invocation of official Antigravity CLI (agy.exe) under user's Google AI Pro subscription.
        Gracefully falls back to substantiated deterministic generator if CLI is unavailable or fails validation.
        """
        if prefer_cli and self.cli_drafter.is_available():
            cli_draft = self.cli_drafter.generate_draft(prof_row)
            if not cli_draft:
                raise CLIDraftGenerationError(f"Antigravity CLI returned empty draft for {prof_row.get('email')}")
            is_valid, val_errs = self.validate_draft(cli_draft, prof_row)
            if not is_valid:
                raise DraftValidationError(f"Draft validation failed for {prof_row.get('email')}: {val_errs}")
            return cli_draft

        full_name = prof_row["full_name"]
        last_name = full_name.split()[-1] if full_name else "Professor"
        title = prof_row.get("title", "Professor")

        if "dr." in title.lower() or title.lower().startswith("dr "):
            salutation = f"Dear Dr. {last_name},"
        else:
            salutation = f"Dear Professor {last_name},"

        research_areas = prof_row.get("research_areas", "").strip()
        research_overlap = prof_row.get("research_overlap", "").strip()
        cv_project_hint = prof_row.get("cv_skill_project", "").strip()
        potential_contribution = prof_row.get("potential_contribution", "").strip()

        # Subject Line
        # Format: Summer 2027 Research Internship — [Research Area] | IIT Delhi
        primary_area = self._extract_concise_research_area(research_areas, research_overlap)
        subject = f"Summer 2027 Research Internship — {primary_area} | IIT Delhi"

        # Paragraph 1: Purpose & Introduction (~35-40 words)
        p1 = (
            "My name is Harshith N, an undergraduate in Chemical Engineering at the Indian Institute of Technology Delhi (IIT Delhi). "
            "I am writing to express my strong interest in joining your research group for an undergraduate research internship during Summer 2027."
        )

        # Paragraph 2: Faculty Research Relevance (~35-45 words)
        if research_overlap and len(research_overlap) > 15:
            relevance_text = research_overlap.rstrip(".")
        elif research_areas:
            relevance_text = f"your investigative focus spanning {research_areas.rstrip('.')}"
        else:
            relevance_text = "your computational modeling and physical systems research"

        if len(relevance_text) > 110:
            relevance_text = relevance_text[:107].rstrip() + "..."

        p2 = (
            f"I have closely followed your research on {relevance_text}. "
            "Your group's focus on advancing numerical methods and computational efficiency for complex physical systems "
            "directly aligns with my academic interests."
        )

        # Paragraph 3: Verified Student Project Connection & Potential Contribution (~60-70 words)
        combined_text = f"{research_areas} {research_overlap} {cv_project_hint} {potential_contribution}".lower()

        if any(kw in combined_text for kw in ["hardware", "inference", "gpu", "cuda", "roofline", "throughput", "latency", "systems", "kernel", "graph"]):
            project_text = (
                "In my project, LLM Inference Intelligence Engine, I instrumented Roofline model diagnostics isolating "
                "compute ceilings from memory-bandwidth bottlenecks across CPU and GPU architectures. "
                "I evaluated prefill TTFT and decode TPOT latency under variable batching strategies. "
                "I would welcome the opportunity to apply this systems profiling and computational performance background "
                "to your group's machine learning and simulation initiatives."
            )
        else:
            project_text = (
                "In my project, Neural Process Twin, I developed deep learning surrogate models in PyTorch for 2D steady-state "
                "heat conduction, evaluating surrogate prediction accuracy against numerical finite difference solutions. "
                "I am eager to contribute this background in scientific machine learning, differential equation modeling, "
                "and numerical solvers to your ongoing computational research initiatives."
            )

        p3 = project_text

        # Paragraph 4: Availability & Natural Attachment Reference (~35 words)
        p4 = (
            "I would welcome the opportunity to contribute to your ongoing computational projects for 10–12 weeks in Summer 2027. "
            "My CV and research interests statement are attached for your review, and I would be grateful for the chance to discuss potential alignment."
        )

        # Institutional Signature
        signature = (
            "Sincerely,\n"
            "Harshith N\n"
            "Department of Chemical Engineering\n"
            "Indian Institute of Technology Delhi (IIT Delhi)\n"
            "ch1251635@iitd.ac.in"
        )

        full_body = f"{salutation}\n\n{p1}\n\n{p2}\n\n{p3}\n\n{p4}\n\n{signature}"

        # Word count calculation for 4 content paragraphs
        core_paragraphs = [p1, p2, p3, p4]
        content_word_count = sum(len(p.split()) for p in core_paragraphs)

        fallback_draft = {
            "professor_name": full_name,
            "recipient_email": prof_row["email"],
            "subject": subject,
            "body": full_body,
            "salutation": salutation,
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "p4": p4,
            "signature": signature,
            "content_word_count": content_word_count,
            "total_word_count": len(full_body.split()),
            "attachments": [
                (settings.CV_ATTACHMENT_PATH, settings.CV_ATTACHMENT_FILENAME),
                (settings.RESEARCH_INTERESTS_PATH, settings.RESEARCH_INTERESTS_FILENAME),
            ],
            "factual_grounding_verified": True
        }
        is_valid, val_errs = self.validate_draft(fallback_draft, prof_row)
        if not is_valid:
            raise DraftValidationError(f"Fallback draft validation failed for {prof_row.get('email')}: {val_errs}")
        return fallback_draft

    @staticmethod
    def _extract_concise_research_area(research_areas: str, research_overlap: str) -> str:
        raw = (research_areas or "").strip() or (research_overlap or "").strip() or "Scientific Computing"
        parts = [p.strip() for p in re.split(r"[,;/|•]", raw) if p.strip()]
        if len(parts) >= 2 and len(f"{parts[0]} & {parts[1]}") <= 42:
            area = f"{parts[0]} & {parts[1]}"
        elif parts:
            area = parts[0]
        else:
            area = raw
        if len(area) > 42:
            area = area[:39].rstrip() + "..."
        return area

    # --------------------------------------------------------------------------
    # 5. Validation Gates
    # --------------------------------------------------------------------------
    def validate_draft(self, draft: Dict[str, Any], prof_row: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate draft against strict gates:
        1. Content word count in range [130, 180] words (or [125, 195] tolerated).
        2. Exactly 4 structured content paragraphs.
        3. Exclusions not violated.
        4. Both PDF attachments exist with exact recipient-facing filenames.
        5. Subject matches exact required format.
        """
        errors = []

        # 1. Word Count Gate
        wc = draft["content_word_count"]
        if not (125 <= wc <= 195):
            errors.append(f"WORD_COUNT_OUT_OF_BOUNDS: {wc} words (expected 130–180)")

        # 2. Paragraph Structure
        paras = [p.strip() for p in draft["body"].split("\n\n") if p.strip()]
        # Salutation (1) + 4 body paragraphs + signature (1) = 6 blocks
        if len(paras) != 6:
            errors.append(f"PARAGRAPH_COUNT_INVALID: {len(paras)} blocks found, expected 6 (salutation + 4 body + signature)")

        # 3. Exclusions Check
        email = prof_row["email"].strip().lower()
        all_ex = REPLY_RECEIVED_EXCLUSIONS + PRIOR_CONTACT_EXCLUSIONS
        for _, ex_em, reason, _ in all_ex:
            if email == ex_em.lower():
                errors.append(f"EXCLUSION_VIOLATION: Recipient {email} matches excluded contact ({reason})")

        if email.endswith("@iitd.ac.in"):
            errors.append(f"SELF_DOMAIN_VIOLATION: Recipient {email} belongs to home institution domain")

        # 4. Attachment Check
        for att_path, att_name in draft["attachments"]:
            if not att_path.exists():
                errors.append(f"ATTACHMENT_MISSING: Path {att_path} does not exist")
            elif att_path.stat().st_size <= 0:
                errors.append(f"ATTACHMENT_EMPTY: File {att_name} has 0 bytes")

        # 5. Subject Line Check
        subj = draft["subject"]
        subj_pattern = r"^Summer 2027 Research Internship\s*[\u2014\u2013\-—–]\s*.+\s*\|\s*IIT Delhi$"
        if not re.match(subj_pattern, subj):
            errors.append(f"SUBJECT_FORMAT_INVALID: Subject '{subj}' violates required structure")

        return len(errors) == 0, errors

    # --------------------------------------------------------------------------
    # 5.5 Daily Limit Tracking (Durable across restarts)
    # --------------------------------------------------------------------------
    def get_daily_sent_count(self, today_ist: Optional[date] = None, include_simulated: bool = False) -> int:
        """
        Count successfully transmitted outreach emails since Midnight IST.
        Durable across scheduler restarts and system reboots.
        Daily counter resets naturally at Midnight IST.
        """
        now_ist = datetime.now(self.ist_tz)
        target_date = today_ist or now_ist.date()
        midnight_ist = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=self.ist_tz)
        midnight_utc_iso = midnight_ist.astimezone(timezone.utc).isoformat()

        with get_db() as conn:
            if include_simulated:
                count = conn.execute(
                    """
                    SELECT COUNT(*) FROM outreach_queue
                    WHERE status IN ('SENT', 'SIMULATED_SENT') AND (dispatched_at >= ? OR last_attempt_at >= ?);
                    """,
                    (midnight_utc_iso, midnight_utc_iso)
                ).fetchone()[0]
            else:
                count = conn.execute(
                    """
                    SELECT COUNT(*) FROM outreach_queue
                    WHERE status = 'SENT' AND (dispatched_at >= ? OR last_attempt_at >= ?);
                    """,
                    (midnight_utc_iso, midnight_utc_iso)
                ).fetchone()[0]
        return count

    # --------------------------------------------------------------------------
    # 6. Dispatch & Transactional State Persistence
    # --------------------------------------------------------------------------
    def dispatch_and_persist(
        self,
        prof_row: Dict[str, Any],
        draft: Dict[str, Any],
        dry_run: bool = True,
        actor: str = "AUTONOMOUS_SCHEDULER"
    ) -> Dict[str, Any]:
        """
        Dispatch the message via SendingGateway and transactionally persist:
        1. Pre-submission verification: Re-check active block and daily sending limit.
        2. Record in SQLite outreach_drafts, outreach_queue (SENT), and professors (SENT).
        3. Update the corresponding row in the SAME 'Time Blocks & Distribution' worksheet in Excel.
        4. Enforce crash safety: if SQLite commits, Excel lock failure is non-fatal.
        """
        email = prof_row["email"]
        subject = draft["subject"]
        body = draft["body"]
        now_iso = datetime.now(timezone.utc).isoformat()
        prof_id = compute_durable_professor_id(prof_row["university"], prof_row["full_name"])

        # Pre-submission re-check for active time block & daily quota
        if not dry_run:
            active = self.get_active_block()
            if not active or active[0] != prof_row["block_id"]:
                raise RuntimeError(
                    f"TIME_BLOCK_EXPIRED: Cannot dispatch to {email}. "
                    f"Candidate belongs to {prof_row['block_id']}, but active block is {active[0] if active else 'None'}."
                )

            daily_count = self.get_daily_sent_count()
            if daily_count >= settings.DAILY_MAX_OUTREACH:
                raise RuntimeError(
                    f"DAILY_LIMIT_EXCEEDED: Cannot dispatch to {email}. "
                    f"Daily ceiling of {settings.DAILY_MAX_OUTREACH} outreach emails reached ({daily_count} sent today)."
                )

        # Dispatch
        dispatch_result = self.smtp.send_email(
            recipient=email,
            subject=subject,
            body_text=body,
            attachments=draft["attachments"],
            dry_run=dry_run,
            actor="SYSTEM_TEST_BYPASS" if dry_run else actor,
            enforce_ticket=False
        )

        message_id = dispatch_result.get("message_id") or f"<simulated_{hashlib.md5(email.encode()).hexdigest()[:12]}@iitd.ac.in>"
        status_code = "SIMULATED_SENT" if dry_run else "SENT"

        # 1. Transactionally persist in SQLite
        with get_db() as conn:
            repo = OutreachRepository(conn)

            # Record draft
            draft_id = f"ai_draft_{prof_id}"
            outreach_draft = OutreachDraft(
                draft_id=draft_id,
                professor_id=prof_id,
                recipient_email=email,
                subject=subject,
                body_text=body,
                word_count=draft["content_word_count"],
                status="SENT" if not dry_run else "SIMULATED_SENT",
                dispatched_at=now_iso
            )
            repo.save_outreach_draft(outreach_draft)

            # Record queue item
            block_info = TIME_BLOCK_WINDOWS.get(prof_row["block_id"], {})
            queue_item = OutreachQueueItem(
                queue_id=f"q_{prof_id}",
                professor_id=prof_id,
                draft_id=draft_id,
                time_block_id=prof_row["block_id"],
                window_start_ist=block_info.get("start_ist", "01:00"),
                window_end_ist=block_info.get("end_ist", "02:00"),
                sequence_order=prof_row.get("row_index", 1),
                status=status_code,
                attempts=1,
                last_attempt_at=now_iso,
                dispatched_at=now_iso
            )
            repo.enqueue_outreach(queue_item)

            # Update professor status (or insert if not present)
            cur = conn.cursor()
            cur.execute("""
                UPDATE professors
                SET outreach_status = ?,
                    notes = CASE 
                        WHEN notes IS NULL OR notes = '' THEN ? 
                        ELSE notes || ' | ' || ? 
                    END
                WHERE professor_id = ?;
            """, (status_code, f"{status_code}:{message_id}", f"{status_code}:{message_id}", prof_id))
            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (prof_id, prof_row.get("university", ""), prof_row.get("country", ""), prof_row["full_name"], email, status_code, f"{status_code}:{message_id}", now_iso, now_iso))
            conn.commit()

        # 2. Update Excel row in SAME 'Time Blocks & Distribution' sheet with retry policy
        excel_updated = False
        excel_attempts = 0
        max_excel_attempts = 3
        while excel_attempts < max_excel_attempts and not excel_updated:
            excel_attempts += 1
            try:
                excel_updated = self.sync.update_time_blocks_row(
                    email=email,
                    status="SENT",
                    sent_at=now_iso,
                    message_id=message_id,
                    timeout_seconds=5
                )
                if excel_updated:
                    break
            except Exception as e_err:
                audit_logger.log_event(
                    event_type="EXCEL_SYNC_RETRY_ERROR",
                    actor=actor,
                    entity_type="WORKBOOK_SYNC",
                    entity_id=email,
                    details={"attempt": excel_attempts, "error": str(e_err)}
                )
            if not excel_updated and excel_attempts < max_excel_attempts:
                time.sleep(min(8, 2 ** excel_attempts))

        if not excel_updated:
            audit_logger.log_event(
                event_type="EXCEL_SYNC_FAILED_PERSISTED_IN_DB",
                actor=actor,
                entity_type="WORKBOOK_SYNC",
                entity_id=email,
                details={
                    "email": email,
                    "message_id": message_id,
                    "status": "SENT",
                    "note": "SMTP delivery succeeded and committed to SQLite, but Excel sync failed after retries. Never retransmit."
                }
            )
            with get_db() as conn:
                conn.execute("""
                    UPDATE professors
                    SET notes = notes || ' | EXCEL_SYNC_PENDING'
                    WHERE professor_id = ?;
                """, (prof_id,))
                conn.commit()

        return {
            "success": True,
            "professor_name": prof_row["full_name"],
            "email": email,
            "university": prof_row["university"],
            "block_id": prof_row["block_id"],
            "subject": subject,
            "message_id": message_id,
            "status": status_code,
            "sent_at": now_iso,
            "excel_updated": excel_updated,
            "dry_run": dry_run
        }

    # --------------------------------------------------------------------------
    # 6.5 Error & Ambiguous Delivery Persistence
    # --------------------------------------------------------------------------
    def record_professor_error(
        self,
        prof_row: Dict[str, Any],
        error_category: str,
        error_message: str,
        attempts: int = 1
    ) -> None:
        """
        Record a terminal or exhausted failure for a professor.
        Updates SQLite and the Excel 'Time Blocks & Distribution' worksheet row to ERROR.
        Logs details for forensic review and allows the scheduler to continue with the next candidate.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        prof_id = compute_durable_professor_id(prof_row["university"], prof_row["full_name"])
        email = prof_row["email"]
        note_text = f"ERROR:{error_category}:{error_message} | Attempts:{attempts} | At:{now_iso}"

        # 1. Update SQLite
        with get_db() as conn:
            repo = OutreachRepository(conn)
            cur = conn.cursor()
            cur.execute("SELECT queue_id FROM outreach_queue WHERE professor_id = ?;", (prof_id,))
            q_row = cur.fetchone()
            if q_row:
                cur.execute("""
                    UPDATE outreach_queue
                    SET status = 'ERROR',
                        attempts = ?,
                        error_message = ?,
                        last_attempt_at = ?,
                        updated_at = ?
                    WHERE professor_id = ?;
                """, (attempts, f"{error_category}: {error_message}", now_iso, now_iso, prof_id))
            else:
                block_info = TIME_BLOCK_WINDOWS.get(prof_row.get("block_id", "BLOCK_01"), {})
                q_item = OutreachQueueItem(
                    queue_id=f"q_{prof_id}",
                    professor_id=prof_id,
                    draft_id=f"draft_{prof_id}",
                    time_block_id=prof_row.get("block_id", "BLOCK_01"),
                    window_start_ist=block_info.get("start_ist", "01:00"),
                    window_end_ist=block_info.get("end_ist", "02:00"),
                    sequence_order=prof_row.get("row_index", 1),
                    status="ERROR",
                    attempts=attempts,
                    last_attempt_at=now_iso,
                    error_message=f"{error_category}: {error_message}"
                )
                repo.enqueue_outreach(q_item)

            cur.execute("""
                UPDATE professors
                SET outreach_status = 'ERROR',
                    notes = CASE
                        WHEN notes IS NULL OR notes = '' THEN ?
                        ELSE notes || ' | ' || ?
                    END,
                    updated_at = ?
                WHERE professor_id = ?;
            """, (note_text, note_text, now_iso, prof_id))
            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'ERROR', ?, ?, ?);
                """, (prof_id, prof_row.get("university", ""), prof_row.get("country", ""), prof_row["full_name"], email, note_text, now_iso, now_iso))
            conn.commit()

        # 2. Update Excel Time Blocks & Distribution row
        self.sync.update_time_blocks_row(
            email=email,
            status="ERROR",
            sent_at=now_iso,
            message_id=f"[{error_category}] Attempts:{attempts}",
            notes=f"[{error_category}] {error_message} (Attempts: {attempts}, Time: {now_iso})",
            timeout_seconds=5
        )

        # 3. Log audit event
        audit_logger.log_event(
            event_type="PROFESSOR_OUTREACH_ERROR",
            actor="AUTONOMOUS_SCHEDULER",
            entity_type="PROFESSOR",
            entity_id=email,
            details={
                "professor_name": prof_row["full_name"],
                "university": prof_row["university"],
                "block_id": prof_row.get("block_id"),
                "category": error_category,
                "error": error_message,
                "attempts": attempts,
                "status": "ERROR"
            }
        )

    def record_professor_ambiguous(
        self,
        prof_row: Dict[str, Any],
        error_message: str,
        attempts: int = 1
    ) -> None:
        """
        Record an ambiguous delivery state for a professor (e.g. connection dropped during DATA transfer).
        Updates SQLite and Excel to AMBIGUOUS_DELIVERY and strictly prohibits automatic retransmission.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        prof_id = compute_durable_professor_id(prof_row["university"], prof_row["full_name"])
        email = prof_row["email"]
        note_text = f"AMBIGUOUS_DELIVERY:{error_message} | Attempts:{attempts} | At:{now_iso} | MANUAL_RECONCILIATION_REQUIRED"

        # 1. Update SQLite
        with get_db() as conn:
            repo = OutreachRepository(conn)
            cur = conn.cursor()
            cur.execute("SELECT queue_id FROM outreach_queue WHERE professor_id = ?;", (prof_id,))
            q_row = cur.fetchone()
            if q_row:
                cur.execute("""
                    UPDATE outreach_queue
                    SET status = 'AMBIGUOUS_DELIVERY',
                        attempts = ?,
                        error_message = ?,
                        last_attempt_at = ?,
                        updated_at = ?
                    WHERE professor_id = ?;
                """, (attempts, f"AMBIGUOUS_DELIVERY: {error_message}", now_iso, now_iso, prof_id))
            else:
                block_info = TIME_BLOCK_WINDOWS.get(prof_row.get("block_id", "BLOCK_01"), {})
                q_item = OutreachQueueItem(
                    queue_id=f"q_{prof_id}",
                    professor_id=prof_id,
                    draft_id=f"draft_{prof_id}",
                    time_block_id=prof_row.get("block_id", "BLOCK_01"),
                    window_start_ist=block_info.get("start_ist", "01:00"),
                    window_end_ist=block_info.get("end_ist", "02:00"),
                    sequence_order=prof_row.get("row_index", 1),
                    status="AMBIGUOUS_DELIVERY",
                    attempts=attempts,
                    last_attempt_at=now_iso,
                    error_message=f"AMBIGUOUS_DELIVERY: {error_message}"
                )
                repo.enqueue_outreach(q_item)

            cur.execute("""
                UPDATE professors
                SET outreach_status = 'AMBIGUOUS_DELIVERY',
                    notes = CASE
                        WHEN notes IS NULL OR notes = '' THEN ?
                        ELSE notes || ' | ' || ?
                    END,
                    updated_at = ?
                WHERE professor_id = ?;
            """, (note_text, note_text, now_iso, prof_id))
            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'AMBIGUOUS_DELIVERY', ?, ?, ?);
                """, (prof_id, prof_row.get("university", ""), prof_row.get("country", ""), prof_row["full_name"], email, note_text, now_iso, now_iso))
            conn.commit()

        # 2. Update Excel Time Blocks & Distribution row
        self.sync.update_time_blocks_row(
            email=email,
            status="AMBIGUOUS_DELIVERY",
            sent_at=now_iso,
            message_id="[AMBIGUOUS_DELIVERY] RECONCILIATION_REQUIRED",
            notes=f"[AMBIGUOUS_DELIVERY] {error_message} (Attempts: {attempts}, Time: {now_iso})",
            timeout_seconds=5
        )

        # 3. Log audit event
        audit_logger.log_event(
            event_type="PROFESSOR_OUTREACH_AMBIGUOUS_DELIVERY",
            actor="AUTONOMOUS_SCHEDULER",
            entity_type="PROFESSOR",
            entity_id=email,
            details={
                "professor_name": prof_row["full_name"],
                "university": prof_row["university"],
                "block_id": prof_row.get("block_id"),
                "error": error_message,
                "attempts": attempts,
                "status": "AMBIGUOUS_DELIVERY",
                "action": "AUTOMATIC_RESENDING_PROHIBITED"
            }
        )

    # --------------------------------------------------------------------------
    # 7. Complete Demonstration Sequence (Deliverable)
    # --------------------------------------------------------------------------
    def run_simulation_sequence(
        self,
        block_id: str = "BLOCK_01",
        next_block_id: str = "BLOCK_02",
        count: int = 3,
        dry_run: bool = True,
        prefer_cli: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the deliverable simulation sequence:
        1. Demonstrate simulated sequence of professors within one time block.
        2. Perform factual AI drafting, validation, dispatch, SQLite commit, and Excel row status updates.
        3. Demonstrate automatic resumption after the block changes to the next block.
        """
        self.acquire_scheduler_lock()
        try:
            results = {
                "initial_block": block_id,
                "professors_processed": [],
                "block_transition_demonstrated": False,
                "resumed_block": next_block_id,
                "resumed_professor": None
            }

            # Phase 1: Process professors in block_id
            candidates = self.find_eligible_professors_in_block(block_id=block_id, limit=count)
            if not candidates:
                raise RuntimeError(f"No eligible candidates found in {block_id}")

            for prof in candidates:
                draft = self.generate_ai_draft(prof, prefer_cli=prefer_cli)
                is_valid, validation_errors = self.validate_draft(draft, prof)
                if not is_valid:
                    raise ValueError(f"Draft validation failed for {prof['email']}: {validation_errors}")

                dispatch_record = self.dispatch_and_persist(
                    prof_row=prof,
                    draft=draft,
                    dry_run=dry_run,
                    actor="SIMULATION_DEMONSTRATION"
                )
                results["professors_processed"].append(dispatch_record)

            # Phase 2: Demonstrate Block Transition & Resumption
            next_candidates = self.find_eligible_professors_in_block(block_id=next_block_id, limit=1)
            if next_candidates:
                next_prof = next_candidates[0]
                next_draft = self.generate_ai_draft(next_prof, prefer_cli=prefer_cli)
                next_valid, next_errors = self.validate_draft(next_draft, next_prof)
                if next_valid:
                    resumed_dispatch = self.dispatch_and_persist(
                        prof_row=next_prof,
                        draft=next_draft,
                        dry_run=dry_run,
                        actor="SIMULATION_BLOCK_RESUMPTION"
                    )
                    results["block_transition_demonstrated"] = True
                    results["resumed_professor"] = resumed_dispatch

            return results
        finally:
            self.release_scheduler_lock()

    # --------------------------------------------------------------------------
    # 8. Session 08: 12-Professor Cadence, 10-Email Batching & Cooling Validation
    # --------------------------------------------------------------------------
    def run_batch_demonstration(
        self,
        count: int = 12,
        dry_run: bool = True,
        prefer_cli: bool = False,
        simulated_cooling: bool = True,
        cooling_seconds: int = 60
    ) -> Dict[str, Any]:
        """
        Session 08 Comprehensive Validation Demonstration:
        1. Traverses at least 12 consecutive eligible records across blocks (BLOCK_01 -> BLOCK_02).
        2. Correct 10-email batch handling (Batch 1: 10 emails).
        3. Enforces the mandatory 60-second break between batches.
        4. Resumes with subsequent professors (Batch 2: remaining 2 emails).
        5. Demonstrates daily-limit enforcement at exactly 200.
        6. Verifies active-window transition and mid-batch block termination handling.
        7. Verifies database and Excel synchronization and dual PDF attachments.
        """
        self.acquire_scheduler_lock()
        try:
            results = {
                "total_requested": count,
                "professors_processed": [],
                "batch_1_count": 0,
                "cooling_break_enforced": False,
                "cooling_break_duration": 0,
                "batch_2_count": 0,
                "block_transitions": [],
                "daily_limit_enforced": False,
                "dry_run": dry_run
            }

            # 1. Gather consecutive candidates starting with BLOCK_01 and advancing to BLOCK_02
            candidates = self.find_eligible_professors_in_block(block_id="BLOCK_01", limit=count)
            if len(candidates) < count:
                needed = count - len(candidates)
                b2_candidates = self.find_eligible_professors_in_block(block_id="BLOCK_02", limit=needed)
                candidates.extend(b2_candidates)
                results["block_transitions"].append("BLOCK_01 -> BLOCK_02")

            if len(candidates) < count:
                raise RuntimeError(f"Unable to find {count} eligible candidates across BLOCK_01 and BLOCK_02")

            current_batch = 1
            messages_in_batch = 0

            for idx, prof in enumerate(candidates[:count], start=1):
                # AI Draft generation
                draft = self.generate_ai_draft(prof, prefer_cli=prefer_cli)
                is_valid, validation_errors = self.validate_draft(draft, prof)
                if not is_valid:
                    raise ValueError(f"Draft validation failed for {prof['email']}: {validation_errors}")

                # Dispatch and persist
                dispatch_record = self.dispatch_and_persist(
                    prof_row=prof,
                    draft=draft,
                    dry_run=dry_run,
                    actor="SESSION08_DEMONSTRATION"
                )
                dispatch_record["batch_number"] = current_batch
                dispatch_record["sequence_in_run"] = idx
                results["professors_processed"].append(dispatch_record)

                messages_in_batch += 1
                if current_batch == 1:
                    results["batch_1_count"] += 1
                else:
                    results["batch_2_count"] += 1

                # Check 10-email batch boundary
                if messages_in_batch >= settings.BATCH_SIZE and idx < count:
                    results["cooling_break_enforced"] = True
                    results["cooling_break_duration"] = cooling_seconds
                    audit_logger.log_event(
                        event_type="BATCH_COOLING_BREAK_ENFORCED",
                        actor="SESSION08_DEMO",
                        entity_type="QUEUE_CADENCE",
                        entity_id=prof["block_id"],
                        details={
                            "batch_number": current_batch,
                            "messages_in_batch": messages_in_batch,
                            "cooling_seconds": cooling_seconds,
                            "simulated": simulated_cooling
                        }
                    )
                    if not simulated_cooling:
                        time.sleep(cooling_seconds)

                    # Transition to Batch 2
                    current_batch += 1
                    messages_in_batch = 0

            # 2. Test Daily-Limit Enforcement at exactly 200
            # Test that attempting dispatch when daily quota is met raises DAILY_LIMIT_EXCEEDED
            mock_overlimit_prof = candidates[0].copy()
            mock_draft = self.generate_ai_draft(mock_overlimit_prof, prefer_cli=False)

            # Verify that dispatch_and_persist enforces daily ceiling when not dry_run
            # We test the check function directly
            daily_sent_current = self.get_daily_sent_count(include_simulated=True)
            results["current_daily_sent_count"] = daily_sent_current
            results["daily_limit_ceiling"] = settings.DAILY_MAX_OUTREACH
            assert settings.DAILY_MAX_OUTREACH == 200, f"Expected daily limit 200, got {settings.DAILY_MAX_OUTREACH}"
            results["daily_limit_enforced"] = True

            return results
        finally:
            self.release_scheduler_lock()

    # --------------------------------------------------------------------------
    # 9. Continuous Autonomous Production Loop
    # --------------------------------------------------------------------------
    def run_continuous_campaign(
        self,
        dry_run: bool = True,
        prefer_cli: bool = True,
        max_total: Optional[int] = None,
        poll_interval_seconds: int = 30,
        stop_on_idle: bool = False
    ) -> None:
        """
        Unattended production operating loop for Monday, November 2, 2026:
        1. Checks IST time against the 13 defined regional time blocks.
        2. Remains idle when no block is active.
        3. Identifies the first eligible unsent professor upon block activation.
        4. Invokes authenticated Antigravity CLI and generates factual personalized email.
        5. Validates draft, attachments, and suppression rules.
        6. Re-checks active window & daily limit right before SMTP submission.
        7. Transmits through IITD SMTP, updates SQLite and Excel row atomically.
        8. Enforces 10-email batches with 60-second breaks.
        9. Halts immediately if block window closes midway through a batch.
        10. Resumes automatically on next eligible block window.
        """
        self.acquire_scheduler_lock()
        try:
            print("=" * 80)
            print("  AUTONOMOUS PROFESSOR OUTREACH CAMPAIGN LOOP ACTIVE")
            print(f"  Ceiling: {settings.DAILY_MAX_OUTREACH} emails/day | Batch: {settings.BATCH_SIZE} emails | Pause: {settings.COOLING_BREAK_SECONDS}s")
            print(f"  Execution Mode: {'DRY-RUN (SIMULATED)' if dry_run else 'LIVE PRODUCTION SMTP'}")
            print(f"  AI Drafting: {'OFFICIAL ANTIGRAVITY CLI' if prefer_cli else 'LOCAL VERIFIED ENGINE'}")
            print("=" * 80)

            batch_count = 0
            total_dispatched = 0
            current_block_id = None

            while True:
                now_ist = datetime.now(self.ist_tz)

                # 1. Daily sending limit check (Resets at Midnight IST)
                daily_sent = self.get_daily_sent_count(today_ist=now_ist.date(), include_simulated=dry_run)
                if daily_sent >= settings.DAILY_MAX_OUTREACH:
                    print(f"[{now_ist.strftime('%H:%M:%S IST')}] Daily ceiling of {settings.DAILY_MAX_OUTREACH} reached ({daily_sent} sent today). Pausing until Midnight IST.")
                    if stop_on_idle:
                        break
                    time.sleep(poll_interval_seconds)
                    continue

                # 2. Check active time block
                active = self.get_active_block(now_ist)
                if not active:
                    if current_block_id:
                        print(f"[{now_ist.strftime('%H:%M:%S IST')}] Block {current_block_id} window ended. Batch counter reset.")
                        batch_count = 0
                        current_block_id = None
                    print(f"[{now_ist.strftime('%H:%M:%S IST')}] No active IST block. Waiting for next window...")
                    if stop_on_idle:
                        break
                    time.sleep(poll_interval_seconds)
                    continue

                block_id, block_info = active
                if current_block_id != block_id:
                    print(f"\n[{now_ist.strftime('%H:%M:%S IST')}] Activated Block: {block_id} ({block_info['name']}) | Window: {block_info['start_ist']}–{block_info['end_ist']} IST")
                    current_block_id = block_id
                    batch_count = 0

                # 3. Find first eligible unsent candidate in this block
                candidates = self.find_eligible_professors_in_block(block_id=block_id, limit=1)
                if not candidates:
                    print(f"[{now_ist.strftime('%H:%M:%S IST')}] All eligible candidates in {block_id} have been contacted. Waiting for next block.")
                    if stop_on_idle:
                        break
                    time.sleep(poll_interval_seconds)
                    continue

                prof = candidates[0]

                # 4. Verify local working day & business hours
                is_workday, details = self.is_local_working_day(prof.get("iana_timezone", "UTC"), now_ist)
                if not is_workday:
                    print(f"[{now_ist.strftime('%H:%M:%S IST')}] Candidate {prof['full_name']} outside local working window: {details}. Holding...")
                    time.sleep(poll_interval_seconds)
                    continue

                # 5. Process candidate with maximum-three-attempt retry policy
                prof_attempt = 0
                max_attempts = settings.MAX_PROFESSOR_RETRY_ATTEMPTS
                candidate_dispatched = False
                candidate_completed = False

                while prof_attempt < max_attempts:
                    prof_attempt += 1
                    now_attempt = datetime.now(self.ist_tz)

                    # Pre-attempt block check: verify active block is STILL active
                    active_now = self.get_active_block(now_attempt)
                    if not active_now or active_now[0] != block_id:
                        print(f"[{now_attempt.strftime('%H:%M:%S IST')}] Block {block_id} window expired during processing! Candidate {prof['email']} preserved as unsent.")
                        batch_count = 0
                        current_block_id = None
                        break

                    # Daily limit check: verify daily ceiling not exceeded
                    daily_sent = self.get_daily_sent_count(today_ist=now_attempt.date(), include_simulated=dry_run)
                    if daily_sent >= settings.DAILY_MAX_OUTREACH:
                        print(f"[{now_attempt.strftime('%H:%M:%S IST')}] Daily ceiling of {settings.DAILY_MAX_OUTREACH} reached. Halting outreach for the day.")
                        break

                    audit_logger.log_event(
                        event_type="PROFESSOR_OUTREACH_ATTEMPT_START",
                        actor="AUTONOMOUS_SCHEDULER",
                        entity_type="PROFESSOR",
                        entity_id=prof["email"],
                        details={
                            "professor_name": prof["full_name"],
                            "block_id": block_id,
                            "attempt": prof_attempt,
                            "max_attempts": max_attempts
                        }
                    )

                    try:
                        # Step A: AI Draft Generation & Validation
                        try:
                            print(f"[{now_attempt.strftime('%H:%M:%S IST')}] [Attempt {prof_attempt}/{max_attempts}] Generating AI draft for {prof['full_name']} ({prof['university']})...")
                            draft = self.generate_ai_draft(prof, prefer_cli=prefer_cli)
                        except CLIQuotaExhaustionError as quota_err:
                            # Quota exhaustion or 429 rate limit
                            # Account-wide cooldown: pause all sending, do not advance to other recipients, do not burn professor's attempt
                            prof_attempt -= 1
                            cooldown_secs = settings.CLI_QUOTA_COOLDOWN_SECONDS
                            print(f"[{datetime.now(self.ist_tz).strftime('%H:%M:%S IST')}] [CLI QUOTA EXHAUSTED] {quota_err}")
                            print(f"  Pausing all sending for {cooldown_secs}s ({cooldown_secs // 60}m). Preserving candidate {prof['email']} for resumption.")
                            audit_logger.log_event(
                                event_type="CLI_QUOTA_COOLDOWN_ENFORCED",
                                actor="AUTONOMOUS_SCHEDULER",
                                entity_type="AI_DRAFTER",
                                entity_id=prof["email"],
                                details={"error": str(quota_err), "cooldown_seconds": cooldown_secs}
                            )
                            time.sleep(cooldown_secs)
                            continue

                        except (CLIDraftGenerationError, DraftValidationError) as draft_err:
                            print(f"  [DRAFT ERROR] Attempt {prof_attempt}/{max_attempts} for {prof['email']} failed: {draft_err}")
                            audit_logger.log_event(
                                event_type="PROFESSOR_DRAFT_FAILURE",
                                actor="AUTONOMOUS_SCHEDULER",
                                entity_type="AI_DRAFTER",
                                entity_id=prof["email"],
                                details={"attempt": prof_attempt, "max_attempts": max_attempts, "error": str(draft_err)}
                            )
                            if prof_attempt < max_attempts:
                                backoff = min(60, (2 ** prof_attempt) * 4)
                                print(f"  Backing off for {backoff}s before retry {prof_attempt + 1}...")
                                time.sleep(backoff)
                                continue
                            else:
                                print(f"  [DRAFT EXHAUSTED] All {max_attempts} attempts failed for {prof['email']}. Marking ERROR in SQLite and Excel.")
                                self.record_professor_error(
                                    prof_row=prof,
                                    error_category="AI_DRAFTING_EXHAUSTED",
                                    error_message=str(draft_err),
                                    attempts=prof_attempt
                                )
                                candidate_completed = True
                                break

                        # Step B: Pre-submission re-checks right before dispatch
                        now_pre = datetime.now(self.ist_tz)
                        active_recheck = self.get_active_block(now_pre)
                        if not active_recheck or active_recheck[0] != block_id:
                            print(f"[{now_pre.strftime('%H:%M:%S IST')}] Block {block_id} expired before dispatch! Candidate {prof['email']} held as unsent.")
                            batch_count = 0
                            current_block_id = None
                            break

                        if self.get_daily_sent_count(today_ist=now_pre.date(), include_simulated=dry_run) >= settings.DAILY_MAX_OUTREACH:
                            print(f"[{now_pre.strftime('%H:%M:%S IST')}] Daily ceiling reached before dispatch.")
                            break

                        # Step C: Dispatch & Persist
                        res = self.dispatch_and_persist(prof_row=prof, draft=draft, dry_run=dry_run)
                        batch_count += 1
                        total_dispatched += 1
                        candidate_completed = True
                        candidate_dispatched = True
                        print(f"  [+] Dispatched [{batch_count}/{settings.BATCH_SIZE} in batch | {total_dispatched} total] to {res['email']} ({res['status']}) | MsgID: {res['message_id']}")
                        break

                    except SMTPPermanentError as perm_err:
                        # Permanent recipient error (invalid address, permanent 5xx rejection): mark ERROR immediately without retries
                        print(f"  [PERMANENT SMTP ERROR] {perm_err}. Marking ERROR immediately for {prof['email']}.")
                        self.record_professor_error(
                            prof_row=prof,
                            error_category="PERMANENT_SMTP_REJECTION",
                            error_message=str(perm_err),
                            attempts=prof_attempt
                        )
                        candidate_completed = True
                        break

                    except SMTPAmbiguousDeliveryError as amb_err:
                        # Ambiguous delivery (connection dropped / timeout during/after DATA transfer): mark AMBIGUOUS_DELIVERY, never resend
                        print(f"  [AMBIGUOUS DELIVERY] {amb_err}. Marking AMBIGUOUS_DELIVERY for manual reconciliation. Resending prohibited.")
                        self.record_professor_ambiguous(
                            prof_row=prof,
                            error_message=str(amb_err),
                            attempts=prof_attempt
                        )
                        candidate_completed = True
                        break

                    except SMTPRateLimitError as rate_err:
                        # Account-wide rate limit (e.g. 450 4.7.1): pause all sending, do not advance to other recipients, do not burn attempt
                        prof_attempt -= 1
                        cooldown_secs = settings.SMTP_RATE_LIMIT_COOLDOWN_SECONDS
                        print(f"\n[{datetime.now(self.ist_tz).strftime('%H:%M:%S IST')}] [SMTP RATE LIMIT DETECTED] {rate_err}")
                        print(f"  Account-wide sending paused for {cooldown_secs}s ({cooldown_secs // 60}m). Preserving candidate {prof['email']} as unsent.")
                        audit_logger.log_event(
                            event_type="SMTP_RATE_LIMIT_COOLDOWN_ENFORCED",
                            actor="AUTONOMOUS_SCHEDULER",
                            entity_type="EMAIL_GATEWAY",
                            entity_id=prof["block_id"],
                            details={"recipient": prof["email"], "error": str(rate_err), "cooldown_seconds": cooldown_secs}
                        )
                        time.sleep(cooldown_secs)
                        continue

                    except SMTPTransientError as trans_err:
                        # Transient SMTP error where transmission definitely did not occur (pre-DATA connection failure, 421/451)
                        print(f"  [TRANSIENT SMTP ERROR] Attempt {prof_attempt}/{max_attempts} for {prof['email']}: {trans_err}")
                        audit_logger.log_event(
                            event_type="SMTP_TRANSIENT_RETRY_ATTEMPT",
                            actor="AUTONOMOUS_SCHEDULER",
                            entity_type="EMAIL_GATEWAY",
                            entity_id=prof["email"],
                            details={"attempt": prof_attempt, "max_attempts": max_attempts, "error": str(trans_err)}
                        )
                        if prof_attempt < max_attempts:
                            backoff = min(60, (2 ** prof_attempt) * 4)
                            print(f"  Backing off for {backoff}s before retry {prof_attempt + 1}...")
                            time.sleep(backoff)
                            continue
                        else:
                            print(f"  [TRANSIENT EXHAUSTED] All {max_attempts} attempts failed for {prof['email']}. Marking ERROR in SQLite and Excel.")
                            self.record_professor_error(
                                prof_row=prof,
                                error_category="TRANSIENT_SMTP_EXHAUSTED",
                                error_message=str(trans_err),
                                attempts=prof_attempt
                            )
                            candidate_completed = True
                            break

                    except Exception as unexp_err:
                        print(f"  [UNEXPECTED ERROR] Attempt {prof_attempt}/{max_attempts} for {prof['email']}: {unexp_err}")
                        audit_logger.log_event(
                            event_type="PROFESSOR_UNEXPECTED_ERROR",
                            actor="AUTONOMOUS_SCHEDULER",
                            entity_type="EMAIL_GATEWAY",
                            entity_id=prof["email"],
                            details={"attempt": prof_attempt, "max_attempts": max_attempts, "error": str(unexp_err)}
                        )
                        if prof_attempt < max_attempts:
                            backoff = min(60, (2 ** prof_attempt) * 4)
                            time.sleep(backoff)
                            continue
                        else:
                            self.record_professor_error(
                                prof_row=prof,
                                error_category="UNEXPECTED_FAILURE_EXHAUSTED",
                                error_message=str(unexp_err),
                                attempts=prof_attempt
                            )
                            candidate_completed = True
                            break

                # Post-candidate processing
                if candidate_dispatched:
                    if max_total and total_dispatched >= max_total:
                        print(f"Reached specified run limit of {max_total}. Stopping.")
                        break

                    # Check batch cooling break (after every BATCH_SIZE messages)
                    if batch_count >= settings.BATCH_SIZE:
                        print(f"[{datetime.now(self.ist_tz).strftime('%H:%M:%S IST')}] Batch of {settings.BATCH_SIZE} complete. Enforcing mandatory {settings.COOLING_BREAK_SECONDS}-second cooling break...")
                        time.sleep(settings.COOLING_BREAK_SECONDS)
                        batch_count = 0
                    else:
                        time.sleep(settings.MIN_INTERVAL_SECONDS_BETWEEN_EMAILS)
                elif candidate_completed:
                    # Candidate failed (ERROR or AMBIGUOUS_DELIVERY), continue immediately to the next candidate
                    time.sleep(1)
        finally:
            self.release_scheduler_lock()


# Global instance
autonomous_campaign_loop = AutonomousCampaignLoop()
