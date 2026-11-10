"""
Opportunity Intelligence Engine: Bounce Intelligence and Verified Email Replacement Module.

Classifies bounce events into diagnostic failure categories:
- INVALID_MAILBOX / RECIPIENT_UNKNOWN
- TEMPORARY_SERVER_FAILURE
- ACCESS_DENIED_POLICY
- RATE_LIMITED
- DOMAIN_MISCONFIG
- MAILBOX_FULL
- TRANSPORT_TIMEOUT

Enforces safety workflow:
1. Suppresses invalid email address in suppression_list and database/Excel.
2. Identifies publicly verified replacement address for the same faculty/organization.
3. Records verification evidence and timestamp.
4. Distinguishes policy rejections from invalid mailboxes.
5. Manages operator review queue and authorized replacement dispatch.
"""

import logging
import re
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from config.settings import settings
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)


class BounceDiagnosticType(str, Enum):
    INVALID_MAILBOX = "INVALID_MAILBOX"
    RECIPIENT_UNKNOWN = "RECIPIENT_UNKNOWN"
    TEMPORARY_SERVER_FAILURE = "TEMPORARY_SERVER_FAILURE"
    ACCESS_DENIED_POLICY = "ACCESS_DENIED_POLICY"
    RATE_LIMITED = "RATE_LIMITED"
    DOMAIN_MISCONFIG = "DOMAIN_MISCONFIG"
    MAILBOX_FULL = "MAILBOX_FULL"
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    UNKNOWN = "UNKNOWN"


@dataclass
class BounceRecoveryRecord:
    id: int
    professor_id: str
    professor_name: str
    original_bounced_email: str
    proposed_email: Optional[str]
    replacement_status: str
    diagnostic_type: BounceDiagnosticType
    smtp_error_snippet: str
    failure_interpretation: str
    current_institution: str
    department: str
    source_url: str
    evidence_notes: str
    is_replacement_eligible: bool
    requires_human_review: bool
    researched_at: str


class BounceRecoveryService:
    """
    Manages bounce diagnostics, suppression, and verified email replacement workflows.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.SQLITE_DB_PATH

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def classify_bounce_diagnostic(error_code_or_msg: str) -> Tuple[BounceDiagnosticType, str]:
        """
        Classifies SMTP / DSN error into distinct failure interpretations.
        """
        s = (error_code_or_msg or "").lower()

        if any(k in s for k in ["550 5.1.1", "user unknown", "no such user", "mailbox unavailable", "recipient address rejected", "user not found", "does not exist", "invalid recipient"]):
            return BounceDiagnosticType.INVALID_MAILBOX, "Mailbox does not exist on target mail server. Address is permanently invalid."
            
        if any(k in s for k in ["5.7.1", "access denied", "policy rejection", "blocked using", "spamhaus", "barracuda", "spf", "dkim", "dmarc", "blacklisted"]):
            return BounceDiagnosticType.ACCESS_DENIED_POLICY, "Server policy or reputation filter rejected message. Mailbox may be valid but inbound policy blocked delivery."
            
        if any(k in s for k in ["450", "451", "4.4.2", "greylisted", "try again later", "temporarily deferred", "rate limit", "too many connections", "too fast"]):
            return BounceDiagnosticType.RATE_LIMITED, "Rate limit or greylisting temporary deferral. Not a mailbox existence failure."
            
        if any(k in s for k in ["552", "5.2.2", "mailbox full", "quota exceeded", "storage allocation exceeded"]):
            return BounceDiagnosticType.MAILBOX_FULL, "Recipient mailbox is over quota. Account exists but cannot receive new mail."
            
        if any(k in s for k in ["timed out", "timeout", "connection refused", "network unreachable"]):
            return BounceDiagnosticType.TRANSPORT_TIMEOUT, "Transport or socket connection timeout. Mail exchange server unreachable."
            
        if any(k in s for k in ["5.1.2", "host not found", "dns query failed", "domain not found"]):
            return BounceDiagnosticType.DOMAIN_MISCONFIG, "Domain DNS or MX records could not be resolved."
            
        if "5" in s and any(k in s for k in ["reject", "failed", "error"]):
            return BounceDiagnosticType.RECIPIENT_UNKNOWN, "Generic permanent delivery rejection (5xx)."
            
        return BounceDiagnosticType.UNKNOWN, "Delivery outcome could not be conclusively determined."

    def list_recovery_records(self) -> List[Dict[str, Any]]:
        """
        Retrieves all bounce recovery records enriched with diagnostic interpretation and replacement status.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    id, professor_id, professor_name, original_bounced_email,
                    proposed_email, replacement_status, current_institution,
                    department, source_url, evidence_notes, researched_at
                FROM proposed_email_replacements
                ORDER BY id ASC
            """)
            rows = cur.fetchall()
            
            results = []
            for r in rows:
                diag_type, diag_desc = self.classify_bounce_diagnostic(r["evidence_notes"] or "")
                
                status = r["replacement_status"] or "UNVERIFIED"
                has_replacement = bool(r["proposed_email"])
                is_eligible = (status == "PROPOSED_REPLACEMENT" and has_replacement)
                needs_review = (status == "NEEDS_REVIEW" or not has_replacement)
                
                rec = {
                    "id": r["id"],
                    "professor_id": r["professor_id"],
                    "professor_name": r["professor_name"],
                    "original_bounced_email": r["original_bounced_email"],
                    "proposed_email": r["proposed_email"],
                    "replacement_status": status,
                    "diagnostic_type": diag_type.value,
                    "failure_interpretation": diag_desc,
                    "current_institution": r["current_institution"],
                    "department": r["department"],
                    "source_url": r["source_url"],
                    "evidence_notes": r["evidence_notes"],
                    "is_replacement_eligible": is_eligible,
                    "requires_human_review": needs_review,
                    "researched_at": r["researched_at"]
                }
                results.append(rec)
            return results
        finally:
            conn.close()

    def suppress_invalid_email(self, email_address: str, reason: str = "PERMANENT_HARD_BOUNCE") -> bool:
        """
        Adds an email address to suppression_list and updates professor record.
        """
        clean_email = email_address.strip().lower()
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # 1. Insert into suppression_list
            cur.execute("""
                INSERT OR IGNORE INTO suppression_list (email, reason, suppressed_at, source)
                VALUES (?, ?, ?, 'BOUNCE_RECOVERY_ENGINE')
            """, (clean_email, reason, now_iso))
            
            # 2. Update professor email verification status
            cur.execute("""
                UPDATE professors
                SET email_verification_status = 'SUPPRESSED_INVALID',
                    notes = notes || ' [SUPPRESSED_HARD_BOUNCE: ' || ? || ']'
                WHERE LOWER(email) = ?
            """, (now_iso, clean_email))
            
            # 3. Update unified queue
            cur.execute("""
                UPDATE unified_outreach_queue
                SET queue_status = 'SUPPRESSED_INVALID'
                WHERE LOWER(recipient_email) = ?
            """, (clean_email,))
            
            conn.commit()
            logger.info(f"Suppressed permanently invalid email: {clean_email}")
            return True
        finally:
            conn.close()

    def authorize_replacement(self, replacement_id: int) -> Dict[str, Any]:
        """
        Authorizes verified replacement email for outreach dispatch.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM proposed_email_replacements WHERE id = ?", (replacement_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Proposed replacement ID {replacement_id} not found")
                
            orig_email = row["original_bounced_email"]
            new_email = row["proposed_email"]
            prof_name = row["professor_name"]
            
            if not new_email:
                raise ValueError(f"No proposed replacement email exists for record {replacement_id}")
                
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # Update proposed_email_replacements status
            cur.execute("""
                UPDATE proposed_email_replacements
                SET replacement_status = 'AUTHORIZED_REPLACEMENT',
                    evidence_notes = evidence_notes || ' [AUTHORIZED_BY_OPERATOR: ' || ? || ']'
                WHERE id = ?
            """, (now_iso, replacement_id))
            
            # Suppress old bounced email
            self.suppress_invalid_email(orig_email, reason="REPLACED_BY_VERIFIED_ALTERNATE")
            
            # Update professor email in professors table
            cur.execute("""
                UPDATE professors
                SET email = ?,
                    email_verification_status = 'VERIFIED',
                    verification_source_url = ?,
                    notes = notes || ' [REPLACED_FROM: ' || ? || ']'
                WHERE LOWER(email) = ?
            """, (new_email, row["source_url"], orig_email, orig_email.lower()))
            
            # Update unified queue
            cur.execute("""
                UPDATE unified_outreach_queue
                SET recipient_email = ?,
                    queue_status = 'AUTHORIZED',
                    updated_at = ?
                WHERE LOWER(recipient_email) = ?
            """, (new_email, now_iso, orig_email.lower()))
            
            conn.commit()
            
            audit_logger.log_event(
                event_type="EMAIL_REPLACEMENT_AUTHORIZED",
                actor="OPERATOR_OR_BOUNCE_RECOVERY",
                entity_type="EMAIL_REPLACEMENT",
                entity_id=str(replacement_id),
                details={
                    "professor_name": prof_name,
                    "original_email": orig_email,
                    "new_authorized_email": new_email
                }
            )
            
            return {
                "success": True,
                "replacement_id": replacement_id,
                "professor_name": prof_name,
                "original_email": orig_email,
                "new_email": new_email,
                "status": "AUTHORIZED_REPLACEMENT"
            }
        finally:
            conn.close()


bounce_recovery_service = BounceRecoveryService()
