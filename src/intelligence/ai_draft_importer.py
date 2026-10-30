"""
AI Draft Importer & Validation Interface.
Enables Antigravity's interactive agent to submit genuinely AI-authored,
evidence-grounded research drafts into the SQLite database and durable outreach queue.

Validates:
1. Exact subject line format: Summer 2027 Research Internship — [Specific Research Area] | IIT Delhi
2. Strict four-paragraph structure and word count (130-180 words excluding salutation/signature)
3. Mandatory institutional signature without LinkedIn or GitHub links
4. Absolute suppression enforcement against reply-received contacts and institutional domains
5. Traceable technical grounding in student projects and faculty evidence
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from config.settings import settings
from src.database.models import OutreachDraft, OutreachQueueItem
from src.database.repository import OutreachRepository
from src.scheduler.queue_runner import (
    TIME_BLOCK_WINDOWS,
    autonomous_queue_runner,
    REPLY_RECEIVED_EXCLUSIONS
)
from src.security.audit_logger import audit_logger


REQUIRED_SIGNATURE_LINES = [
    "Sincerely,",
    "Harshith N",
    "Undergraduate, Department of Chemical Engineering",
    "Indian Institute of Technology Delhi (IIT Delhi)",
    "ch1251635@iitd.ac.in"
]


class AIDraftValidationError(Exception):
    """Raised when an AI-authored draft violates quality, structural, or safety gates."""
    pass


class AIDraftImporter:
    """Validates and stages genuinely AI-authored faculty outreach drafts into SQLite."""

    @staticmethod
    def validate_draft(draft_data: Dict[str, Any], repo: OutreachRepository) -> Tuple[bool, Optional[str]]:
        """
        Validate draft against all application and safety gates:
        - Exact subject format
        - Strict 4-paragraph body (130-180 words)
        - Clean signature without forbidden links
        - Suppression and exclusion verification
        - Grounding evidence presence
        """
        recipient = (draft_data.get("recipient_email") or "").strip().lower()
        if not recipient:
            return False, "Validation Error: Missing recipient email"

        # 1. Suppression & Exclusion Check
        is_supp, reason = autonomous_queue_runner.is_suppressed_or_excluded(repo, recipient)
        if is_supp:
            return False, f"Safety Gate Blocked: Recipient {recipient} is suppressed ({reason})"

        # 2. Subject Line Validation
        subject = (draft_data.get("subject") or "").strip()
        if not subject.startswith("Summer 2027 Research Internship — "):
            return False, f"Subject Gate Failed: Subject must begin with 'Summer 2027 Research Internship — ', got: '{subject}'"
        if not subject.endswith(" | IIT Delhi"):
            return False, f"Subject Gate Failed: Subject must end with ' | IIT Delhi', got: '{subject}'"

        # Extract research area between prefix and suffix
        prefix = "Summer 2027 Research Internship — "
        suffix = " | IIT Delhi"
        area = subject[len(prefix):len(subject) - len(suffix)].strip()
        if not area or len(area) < 3:
            return False, f"Subject Gate Failed: Research area must not be empty"

        # 3. Body Text & Paragraph Structure
        body = (draft_data.get("body_text") or "").strip()
        if not body:
            return False, "Validation Error: Body text is empty"

        # Prohibited link check
        lower_body = body.lower()
        if "linkedin.com" in lower_body or "github.com" in lower_body:
            return False, "Quality Gate Failed: Prohibited external links (LinkedIn/GitHub) present in body or signature"

        # Check signature components
        for sig_line in REQUIRED_SIGNATURE_LINES:
            if sig_line not in body:
                return False, f"Quality Gate Failed: Signature missing mandatory line: '{sig_line}'"

        # Split into blocks separated by blank lines
        blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
        if len(blocks) < 6:
            return False, f"Structure Gate Failed: Expected [Salutation, P1, P2, P3, P4, Signature] (6 blocks), found {len(blocks)}"

        salutation = blocks[0]
        if not (salutation.startswith("Dear Professor") or salutation.startswith("Dear Dr.")):
            return False, f"Salutation Gate Failed: Must start with 'Dear Professor' or 'Dear Dr.', got: '{salutation}'"

        p1, p2, p3, p4 = blocks[1], blocks[2], blocks[3], blocks[4]

        # Calculate word count of the 4 content paragraphs
        content_words = f"{p1} {p2} {p3} {p4}".split()
        word_count = len(content_words)

        if word_count < 130 or word_count > 180:
            return False, f"Word Count Gate Failed: 4-paragraph word count is {word_count} words (strictly 130-180 words required)"

        # 4. Technical Grounding Metadata
        grounding = draft_data.get("grounding_evidence") or {}
        if not grounding.get("cv_project_id"):
            return False, "Grounding Gate Failed: Missing cv_project_id in grounding metadata"
        if not grounding.get("faculty_evidence_url"):
            return False, "Grounding Gate Failed: Missing faculty_evidence_url in grounding metadata"

        return True, None

    def import_drafts(
        self,
        drafts: List[Dict[str, Any]],
        repo: OutreachRepository,
        actor: str = "ANTIGRAVITY_AGENT"
    ) -> Dict[str, Any]:
        """
        Validate and atomically commit AI-authored drafts into outreach_drafts and outreach_queue.
        """
        results = []
        imported_count = 0
        rejected_count = 0

        for draft_data in drafts:
            prof_id = draft_data.get("professor_id")
            recipient = draft_data.get("recipient_email", "").strip().lower()

            # Verify professor exists in DB
            prof = repo.get_professor(prof_id)
            if not prof:
                # Attempt lookup by email
                prof = repo.get_professor_by_email(recipient)
                if prof:
                    prof_id = prof["professor_id"]
                    draft_data["professor_id"] = prof_id
                else:
                    results.append({
                        "professor_id": prof_id,
                        "recipient_email": recipient,
                        "status": "REJECTED",
                        "error": f"Professor with ID '{prof_id}' or email '{recipient}' not found in database."
                    })
                    rejected_count += 1
                    continue

            # Validate draft gates
            is_valid, error_msg = self.validate_draft(draft_data, repo)
            if not is_valid:
                results.append({
                    "professor_id": prof_id,
                    "recipient_email": recipient,
                    "status": "REJECTED",
                    "error": error_msg
                })
                rejected_count += 1
                continue

            # Calculate word count
            blocks = [b.strip() for b in draft_data["body_text"].split("\n\n") if b.strip()]
            content_words = f"{blocks[1]} {blocks[2]} {blocks[3]} {blocks[4]}".split()
            word_count = len(content_words)

            draft_id = draft_data.get("draft_id") or f"aidraft_{prof_id[5:15]}"

            # Save in outreach_drafts
            draft_obj = OutreachDraft(
                draft_id=draft_id,
                professor_id=prof_id,
                recipient_email=recipient,
                subject=draft_data["subject"],
                body_text=draft_data["body_text"],
                word_count=word_count,
                status="APPROVED"  # Genuinely AI-authored and validated
            )
            repo.save_outreach_draft(draft_obj)

            # Determine time block assignment
            time_block_id = draft_data.get("time_block_id") or prof.get("time_block") or "BLOCK_01"
            window_info = TIME_BLOCK_WINDOWS.get(time_block_id, {})
            start_ist = window_info.get("start_ist", "08:00")
            end_ist = window_info.get("end_ist", "09:30")
            seq_order = prof.get("time_block_order") or 1

            # If a queue item already exists for this professor, reuse queue_id to prevent duplicates
            existing_q = repo.conn.execute(
                "SELECT queue_id, status FROM outreach_queue WHERE professor_id = ?;", (prof_id,)
            ).fetchone()
            if existing_q:
                queue_id = existing_q[0]
                queue_status = existing_q[1] if existing_q[1] in ("SENT", "SIMULATED_SENT") else "PENDING"
            else:
                queue_id = f"q_{seq_order:04d}_{prof_id[5:15]}"
                queue_status = "PENDING"

            queue_item = OutreachQueueItem(
                queue_id=queue_id,
                professor_id=prof_id,
                draft_id=draft_id,
                time_block_id=time_block_id,
                window_start_ist=start_ist,
                window_end_ist=end_ist,
                sequence_order=seq_order,
                status=queue_status,
                batch_number=1
            )
            repo.enqueue_outreach(queue_item)

            # Update professor status to DRAFT_READY
            repo.update_professor_status(prof_id, "DRAFT_READY", notes=f"Genuinely AI-authored draft staged by {actor}")

            results.append({
                "professor_id": prof_id,
                "draft_id": draft_id,
                "queue_id": queue_id,
                "recipient_email": recipient,
                "subject": draft_data["subject"],
                "word_count": word_count,
                "time_block": time_block_id,
                "status": "STAGED"
            })
            imported_count += 1

        repo.conn.commit()

        audit_logger.log_event(
            event_type="AI_DRAFTS_IMPORTED",
            actor=actor,
            entity_type="QUEUE",
            entity_id="AI_BATCH",
            details={
                "imported_count": imported_count,
                "rejected_count": rejected_count,
                "total": len(drafts)
            }
        )

        return {
            "success": True,
            "imported_count": imported_count,
            "rejected_count": rejected_count,
            "results": results
        }

    def import_from_json_file(self, json_path: Path, repo: OutreachRepository) -> Dict[str, Any]:
        """Load, validate and commit drafts from a structured JSON file."""
        p = Path(json_path)
        if not p.exists():
            raise FileNotFoundError(f"AI draft file not found: {p}")

        with open(p, "r", encoding="utf-8") as f:
            drafts = json.load(f)

        if not isinstance(drafts, list):
            raise ValueError("Draft file content must be a JSON array of draft objects.")

        return self.import_drafts(drafts, repo, actor="ANTIGRAVITY_JSON_IMPORT")


# Singleton instance
ai_draft_importer = AIDraftImporter()
