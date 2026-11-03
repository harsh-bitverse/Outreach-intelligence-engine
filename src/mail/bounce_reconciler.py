"""
Production Bounce Reconciliation and Email Reverification Module.
Handles RFC 3464 DSN MIME parsing, classification, SQLite ledger tracking,
Excel workbook synchronization, and proposed replacement email storage.
Strictly operates in READ-ONLY mode over IMAP (RFC 3501 EXAMINE) without modifying
message flags or deleting webmail messages.
"""

import email
from email.header import decode_header
import imaplib
import logging
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl
from openpyxl.styles import Font

from config.settings import settings
from src.database.connection import get_db
from src.database.workbook_sync import DynamicWorkbookSync, WorkbookLockError
from src.mail.credentials import CredentialManager
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)


def decode_mime_header(header_val: Optional[str]) -> str:
    """Safely decode RFC 2047 MIME encoded headers."""
    if not header_val:
        return ""
    try:
        decoded_parts = decode_header(header_val)
        parts = []
        for text, encoding in decoded_parts:
            if isinstance(text, bytes):
                try:
                    parts.append(text.decode(encoding or "utf-8", errors="replace"))
                except Exception:
                    parts.append(text.decode("utf-8", errors="replace"))
            else:
                parts.append(str(text))
        return "".join(parts)
    except Exception:
        return str(header_val)


def clean_email_address(raw_addr: Optional[str]) -> str:
    """Extract clean lowercase email address, removing MIME prefixes and angle brackets."""
    if not raw_addr:
        return ""
    s = str(raw_addr).strip()
    s = re.sub(r"^(?:rfc\s*[/;]?\s*822\s*;\s*)", "", s, flags=re.IGNORECASE).strip()
    s = s.strip("<> \t\r\n")
    # In case of "Name <email@domain>"
    m = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", s)
    if m:
        return m.group(0).lower()
    return s.lower()


class BounceClassifier:
    """Accurately classifies email delivery failure notifications."""

    INVALID_PATTERNS = [
        "5.1.1",
        "5.1.10",
        "5.1.3",
        "recipientnotfound",
        "user unknown",
        "unknown address",
        "invalid recipient",
        "address rejected",
        "no such user",
        "mailbox does not exist",
        "account that you tried to reach does not exist",
        "bad recipient address syntax",
        "user does not exist",
    ]

    PERMANENT_OTHER_PATTERNS = [
        "5.4.14",
        "hop count exceeded",
        "mail loop",
        "5.7.1",
        "relay access denied",
        "relay denied",
        "policy rejection",
        "dmarc",
        "spf",
        "dns hard error",
        "bad destination host",
        "5.1.2",
    ]

    @classmethod
    def classify(
        cls,
        action: Optional[str],
        status_code: Optional[str],
        diagnostic_code: Optional[str],
        body_text: Optional[str] = "",
    ) -> str:
        """
        Classifies delivery failure into:
          - 'BOUNCED_INVALID_ADDRESS'
          - 'BOUNCED_PERMANENT_OTHER'
          - 'TEMPORARY_DELIVERY_FAILURE'
          - 'NEEDS_REVIEW'
        """
        act = (action or "").strip().lower()
        stat = (status_code or "").strip().lower()
        diag = (diagnostic_code or "").strip().lower()
        body = (body_text or "").strip().lower()

        # 1. Temporary delivery failure
        if (
            act == "delayed"
            or stat.startswith("4.")
            or "temporary failure" in stat
            or "temporary failure" in diag
            or "4.4.2" in diag
            or "bad connection 'timeout'" in diag
        ):
            return "TEMPORARY_DELIVERY_FAILURE"

        # 2. Permanent other: hop count exceeded, relay/policy rejection, dns hard error
        if any(p in stat for p in ["5.4.14", "5.1.2", "5.7.1"]) or any(
            p in diag for p in cls.PERMANENT_OTHER_PATTERNS
        ):
            return "BOUNCED_PERMANENT_OTHER"

        # 3. Permanent invalid-address failure
        if any(p in diag for p in cls.INVALID_PATTERNS) or any(
            p in stat for p in ["5.1.1", "5.1.10", "5.1.3"]
        ):
            return "BOUNCED_INVALID_ADDRESS"

        # Check body text if diagnostic header was omitted
        if any(p in body for p in cls.INVALID_PATTERNS):
            return "BOUNCED_INVALID_ADDRESS"

        if stat.startswith("5.") or "550" in diag or "permanent" in stat:
            return "BOUNCED_INVALID_ADDRESS"

        return "NEEDS_REVIEW"


class DeliveryStatusNotification:
    """Structured representation of an RFC 3464 DSN."""

    def __init__(
        self,
        folder: str,
        uid: int,
        bounce_message_id: str,
        bounce_date: str,
        bounce_from: str,
        bounce_subject: str,
        action: Optional[str] = None,
        status_code: Optional[str] = None,
        diagnostic_code: Optional[str] = None,
        final_recipient: Optional[str] = None,
        original_recipient: Optional[str] = None,
        remote_mta: Optional[str] = None,
        reporting_mta: Optional[str] = None,
        orig_message_id: Optional[str] = None,
        orig_subject: Optional[str] = None,
        orig_to: Optional[str] = None,
        orig_date: Optional[str] = None,
        human_description: Optional[str] = None,
    ):
        self.folder = folder
        self.uid = uid
        self.bounce_message_id = bounce_message_id
        self.bounce_date = bounce_date
        self.bounce_from = bounce_from
        self.bounce_subject = bounce_subject
        self.action = action
        self.status_code = status_code
        self.diagnostic_code = diagnostic_code
        self.final_recipient = final_recipient
        self.original_recipient = original_recipient
        self.remote_mta = remote_mta
        self.reporting_mta = reporting_mta
        self.orig_message_id = orig_message_id
        self.orig_subject = orig_subject
        self.orig_to = orig_to
        self.orig_date = orig_date
        self.human_description = human_description or ""

        self.recipient_email = clean_email_address(
            self.final_recipient or self.original_recipient or self.orig_to
        )
        self.category = BounceClassifier.classify(
            self.action, self.status_code, self.diagnostic_code, self.human_description
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "folder": self.folder,
            "uid": self.uid,
            "bounce_message_id": self.bounce_message_id,
            "bounce_date": self.bounce_date,
            "recipient_email": self.recipient_email,
            "orig_message_id": self.orig_message_id,
            "orig_subject": self.orig_subject,
            "action": self.action,
            "status_code": self.status_code,
            "diagnostic_code": self.diagnostic_code,
            "category": self.category,
            "remote_mta": self.remote_mta,
            "reporting_mta": self.reporting_mta,
        }


class DSNParser:
    """Parses raw email messages into DeliveryStatusNotification objects."""

    @classmethod
    def parse_message(
        cls, folder: str, uid: int, raw_bytes: bytes
    ) -> Optional[DeliveryStatusNotification]:
        """Parse raw RFC 822 email bytes into a structured DSN object."""
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception as e:
            logger.warning(f"Failed to parse email bytes for UID {uid}: {e}")
            return None

        bounce_mid = msg.get("Message-ID", "")
        bounce_date = msg.get("Date", "")
        bounce_from = decode_mime_header(msg.get("From", ""))
        bounce_subj = decode_mime_header(msg.get("Subject", ""))

        # Check if DSN-like message
        ctype_main = msg.get_content_type().lower()
        is_dsn_candidate = (
            "multipart/report" in ctype_main
            or any(
                term in bounce_from.lower()
                for term in ["mailer-daemon", "postmaster"]
            )
            or any(
                term in bounce_subj.lower()
                for term in [
                    "delivery status",
                    "undelivered",
                    "undeliverable",
                    "failure notice",
                    "returned mail",
                    "unzustellbar",
                ]
            )
        )

        if not is_dsn_candidate:
            return None

        action = None
        status_code = None
        diagnostic_code = None
        final_recipient = None
        original_recipient = None
        remote_mta = None
        reporting_mta = None
        orig_msg_id = None
        orig_subj = None
        orig_to = None
        orig_date = None
        body_texts = []

        for part in msg.walk():
            ctype = part.get_content_type().lower()
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        charset = part.get_content_charset() or "utf-8"
                        body_texts.append(payload.decode(charset, errors="replace"))
                    except Exception:
                        body_texts.append(str(payload))
            elif ctype == "message/delivery-status":
                payload = part.get_payload()
                subparts = payload if isinstance(payload, list) else [payload]
                for sub in subparts:
                    if hasattr(sub, "items"):
                        for k, v in sub.items():
                            kl = k.lower()
                            if kl == "action" and not action:
                                action = v.strip()
                            elif kl == "status" and not status_code:
                                status_code = v.strip()
                            elif kl == "diagnostic-code" and not diagnostic_code:
                                diagnostic_code = v.strip()
                            elif kl == "final-recipient" and not final_recipient:
                                final_recipient = v.strip()
                            elif (
                                kl == "original-recipient" and not original_recipient
                            ):
                                original_recipient = v.strip()
                            elif kl == "remote-mta" and not remote_mta:
                                remote_mta = v.strip()
                            elif kl == "reporting-mta" and not reporting_mta:
                                reporting_mta = v.strip()
                    elif isinstance(sub, str):
                        for line in sub.splitlines():
                            if ":" in line:
                                k, v = line.split(":", 1)
                                kl = k.strip().lower()
                                vl = v.strip()
                                if kl == "action" and not action:
                                    action = vl
                                elif kl == "status" and not status_code:
                                    status_code = vl
                                elif kl == "diagnostic-code" and not diagnostic_code:
                                    diagnostic_code = vl
                                elif kl == "final-recipient" and not final_recipient:
                                    final_recipient = vl
                                elif (
                                    kl == "original-recipient"
                                    and not original_recipient
                                ):
                                    original_recipient = vl
                                elif kl == "remote-mta" and not remote_mta:
                                    remote_mta = vl
                                elif kl == "reporting-mta" and not reporting_mta:
                                    reporting_mta = vl
            elif ctype in ("message/rfc822", "text/rfc822-headers"):
                payload = part.get_payload()
                submsg = None
                if isinstance(payload, list) and len(payload) > 0:
                    submsg = payload[0]
                elif hasattr(payload, "get"):
                    submsg = payload
                elif isinstance(payload, (bytes, str)):
                    raw_sub = part.get_payload(decode=True) or (
                        payload.encode() if isinstance(payload, str) else payload
                    )
                    try:
                        submsg = email.message_from_bytes(raw_sub)
                    except Exception:
                        submsg = None
                if submsg:
                    orig_msg_id = orig_msg_id or submsg.get("Message-ID")
                    orig_subj = orig_subj or decode_mime_header(submsg.get("Subject"))
                    orig_to = orig_to or decode_mime_header(submsg.get("To"))
                    orig_date = orig_date or submsg.get("Date")

        all_body = "\n".join(body_texts)
        if not orig_msg_id and all_body:
            m = re.search(r"Message-ID:\s*(<[^>]+>)", all_body, re.IGNORECASE)
            if m:
                orig_msg_id = m.group(1).strip()
        if not orig_subj and all_body:
            m = re.search(r"Subject:\s*(.+)", all_body, re.IGNORECASE)
            if m:
                orig_subj = decode_mime_header(m.group(1).strip())
        if not orig_to and all_body:
            m = re.search(r"To:\s*(.+)", all_body, re.IGNORECASE)
            if m:
                orig_to = clean_email_address(m.group(1).strip())

        if not status_code and all_body:
            m = re.search(r"Status:\s*([45]\.\d+\.\d+)", all_body, re.IGNORECASE)
            if m:
                status_code = m.group(1).strip()

        if not action and all_body:
            m = re.search(r"Action:\s*(failed|delayed|delivered|relayed)", all_body, re.IGNORECASE)
            if m:
                action = m.group(1).strip()

        if not final_recipient and all_body:
            m = re.search(r"Final-Recipient:\s*(.+)", all_body, re.IGNORECASE)
            if m:
                final_recipient = m.group(1).strip()

        if not diagnostic_code and all_body:
            m = re.search(
                r"(?:Diagnostic-Code|Remote server returned|reason):\s*([^\n\r]+)",
                all_body,
                re.IGNORECASE,
            )
            if m:
                diagnostic_code = m.group(1).strip()
            else:
                m2 = re.search(r"(5\d\d\s+5\.\d+\.\d+[^\r\n]*)", all_body)
                if m2:
                    diagnostic_code = m2.group(1).strip()

        return DeliveryStatusNotification(
            folder=folder,
            uid=uid,
            bounce_message_id=bounce_mid,
            bounce_date=bounce_date,
            bounce_from=bounce_from,
            bounce_subject=bounce_subj,
            action=action,
            status_code=status_code,
            diagnostic_code=diagnostic_code,
            final_recipient=final_recipient,
            original_recipient=original_recipient,
            remote_mta=remote_mta,
            reporting_mta=reporting_mta,
            orig_message_id=orig_msg_id,
            orig_subject=orig_subj,
            orig_to=orig_to,
            orig_date=orig_date,
            human_description=all_body[:1000] if all_body else "",
        )


class BounceReconciler:
    """
    Coordinates IMAP reading, bounce reconciliation across SQLite and Excel,
    and storage of replacement faculty emails.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        workbook_path: Optional[Path] = None,
        imap_host: Optional[str] = None,
        imap_port: Optional[int] = None,
    ):
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.sync = DynamicWorkbookSync(self.workbook_path)
        self.imap_host = imap_host or settings.IITD_IMAP_SERVER
        self.imap_port = imap_port or settings.IITD_IMAP_PORT

    def init_database_tables(self) -> None:
        """Create delivery tracking, checkpoint, and replacement tables in SQLite."""
        with get_db(self.db_path) as conn:
            cur = conn.cursor()

            # 1. Delivery status ledger table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS delivery_status_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    imap_folder TEXT NOT NULL,
                    imap_uid INTEGER NOT NULL,
                    bounce_message_id TEXT,
                    original_message_id TEXT,
                    recipient_email TEXT NOT NULL,
                    professor_id TEXT,
                    category TEXT NOT NULL,
                    action TEXT,
                    status_code TEXT,
                    diagnostic_code TEXT,
                    remote_mta TEXT,
                    reporting_mta TEXT,
                    bounce_received_at TEXT,
                    reconciled_at TEXT NOT NULL,
                    UNIQUE(imap_folder, imap_uid)
                );
            """)

            # 2. Checkpoints table for ongoing automatic monitoring
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bounce_monitor_checkpoints (
                    folder TEXT PRIMARY KEY,
                    uidvalidity INTEGER,
                    last_seen_uid INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
            """)

            # 3. Proposed email replacements table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS proposed_email_replacements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    professor_id TEXT NOT NULL,
                    professor_name TEXT NOT NULL,
                    original_bounced_email TEXT NOT NULL,
                    proposed_email TEXT,
                    replacement_status TEXT NOT NULL, -- PROPOSED_REPLACEMENT, UNVERIFIED, NEEDS_REVIEW
                    current_institution TEXT,
                    department TEXT,
                    source_url TEXT,
                    evidence_notes TEXT,
                    researched_at TEXT NOT NULL,
                    UNIQUE(professor_id)
                );
            """)

            # Create indices
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_dsl_recipient ON delivery_status_ledger(recipient_email);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_dsl_orig_mid ON delivery_status_ledger(original_message_id);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_dsl_category ON delivery_status_ledger(category);"
            )
            conn.commit()

    def scan_imap_mailbox(
        self, folder: str = "INBOX", since_date: Optional[str] = "01-Nov-2026"
    ) -> List[DeliveryStatusNotification]:
        """
        Scan IITD IMAP mailbox in strictly READ-ONLY mode (RFC 3501 EXAMINE).
        Returns parsed DeliveryStatusNotification objects.
        """
        user, pwd = CredentialManager.get_credentials()
        if not user or not pwd:
            raise RuntimeError("IITD IMAP credentials not found in Windows Credential Manager.")

        client = imaplib.IMAP4_SSL(
            self.imap_host, self.imap_port, ssl_context=ssl.create_default_context()
        )
        auth_user = user
        try:
            client.login(auth_user, pwd)
        except imaplib.IMAP4.error as err:
            alt_user = (
                auth_user.split("@")[0]
                if "@" in auth_user
                else f"{auth_user}@iitd.ac.in"
            )
            client.login(alt_user, pwd)
            auth_user = alt_user

        # RFC 3501 EXAMINE ensures flags/read status are NEVER modified
        status, count_data = client.select(folder, readonly=True)
        if status != "OK":
            client.logout()
            raise RuntimeError(f"Could not open mailbox '{folder}' in read-only mode.")

        # Search for all messages since date (or all)
        search_args = ["SINCE", since_date] if since_date else ["ALL"]
        status, data = client.uid("search", None, *search_args)
        if status != "OK" or not data or not data[0]:
            client.logout()
            return []

        uids = [int(u) for u in data[0].split()]
        logger.info(f"Inspecting {len(uids)} IMAP messages in '{folder}' since {since_date}...")

        notifications = []
        for uid in uids:
            # Use BODY.PEEK to prevent setting \Seen flag
            fetch_status, fetch_data = client.uid("fetch", str(uid).encode(), "(BODY.PEEK[])")
            if fetch_status == "OK" and fetch_data and isinstance(fetch_data[0], tuple):
                raw_bytes = fetch_data[0][1]
                dsn = DSNParser.parse_message(folder=folder, uid=uid, raw_bytes=raw_bytes)
                if dsn:
                    # Filter out unrelated non-campaign DSNs:
                    # Must have Summer 2027 in subject/body, or an original message id @iitd.ac.in,
                    # or an original recipient in our sent campaign database
                    if self._is_campaign_dsn(dsn):
                        notifications.append(dsn)

        client.logout()
        return notifications

    def _is_campaign_dsn(self, dsn: DeliveryStatusNotification) -> bool:
        """Determines if a DSN belongs to the Summer 2027 outreach campaign."""
        # 1. Subject match
        if (
            "summer 2027" in (dsn.orig_subject or "").lower()
            or "summer 2027" in (dsn.bounce_subject or "").lower()
            or "summer 2027" in dsn.human_description.lower()
        ):
            return True

        # 2. Outbound message id @iitd.ac.in
        if dsn.orig_message_id and "@iitd.ac.in" in dsn.orig_message_id.lower():
            return True

        # 3. Recipient exists in professors database with outreach status SENT/SIMULATED_SENT
        if dsn.recipient_email:
            with get_db(self.db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM professors WHERE LOWER(email) = ? AND outreach_status IN ('SENT', 'SIMULATED_SENT');",
                    (dsn.recipient_email.lower(),),
                ).fetchone()
                if row:
                    return True

        return False

    def match_professor(
        self, dsn: DeliveryStatusNotification
    ) -> Optional[Dict[str, Any]]:
        """
        Matches a DSN notification against SQLite database records.
        Prefers exact Message-ID match, then recipient email match.
        """
        with get_db(self.db_path) as conn:
            conn.row_factory = openpyxl_row_factory = None  # standard sqlite3 row
            cur = conn.cursor()

            # 1. Try exact match by original Message-ID in email_messages
            if dsn.orig_message_id:
                cur.execute(
                    "SELECT professor_id, recipient, subject, sent_received_at FROM email_messages WHERE message_id = ?;",
                    (dsn.orig_message_id,),
                )
                em = cur.fetchone()
                if em:
                    prof_id = em[0]
                    cur.execute(
                        "SELECT * FROM professors WHERE professor_id = ?;", (prof_id,)
                    )
                    prof = cur.fetchone()
                    if prof:
                        col_names = [d[0] for d in cur.description]
                        return dict(zip(col_names, prof))

            # 2. Try match by recipient email in professors
            if dsn.recipient_email:
                cur.execute(
                    "SELECT * FROM professors WHERE LOWER(email) = ?;",
                    (dsn.recipient_email.lower(),),
                )
                prof = cur.fetchone()
                if prof:
                    col_names = [d[0] for d in cur.description]
                    return dict(zip(col_names, prof))

        return None

    def reconcile_notifications(
        self, notifications: List[DeliveryStatusNotification]
    ) -> Dict[str, Any]:
        """
        Apply reconciliation transactionally to SQLite and Excel.
        Deduplicates repeated DSNs, preserves historical sent ledger,
        and marks invalid addresses to prevent future dispatch.
        """
        self.init_database_tables()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Deduplicate notifications by recipient_email and original_message_id
        deduped_dsns: Dict[str, DeliveryStatusNotification] = {}
        for dsn in notifications:
            key = dsn.recipient_email or dsn.orig_message_id or f"uid_{dsn.uid}"
            if key not in deduped_dsns:
                deduped_dsns[key] = dsn
            else:
                # If existing is TEMPORARY and new is PERMANENT, upgrade
                if deduped_dsns[key].category == "TEMPORARY_DELIVERY_FAILURE" and dsn.category != "TEMPORARY_DELIVERY_FAILURE":
                    deduped_dsns[key] = dsn

        stats = {
            "total_inspected": len(notifications),
            "unique_dsns": len(deduped_dsns),
            "matched_professors": 0,
            "bounced_invalid_address": 0,
            "bounced_permanent_other": 0,
            "temporary_delivery_failure": 0,
            "needs_review": 0,
            "unmatched": 0,
            "sqlite_updated": 0,
            "excel_updated": 0,
            "reconciled_details": [],
        }

        with get_db(self.db_path) as conn:
            cur = conn.cursor()

            for key, dsn in deduped_dsns.items():
                prof = self.match_professor(dsn)
                prof_id = prof["professor_id"] if prof else None
                prof_name = prof["full_name"] if prof else None

                if prof:
                    stats["matched_professors"] += 1
                else:
                    stats["unmatched"] += 1

                category = dsn.category
                if category == "BOUNCED_INVALID_ADDRESS":
                    stats["bounced_invalid_address"] += 1
                elif category == "BOUNCED_PERMANENT_OTHER":
                    stats["bounced_permanent_other"] += 1
                elif category == "TEMPORARY_DELIVERY_FAILURE":
                    stats["temporary_delivery_failure"] += 1
                else:
                    stats["needs_review"] += 1

                # 1. Record in delivery_status_ledger
                cur.execute("""
                    INSERT OR REPLACE INTO delivery_status_ledger (
                        imap_folder, imap_uid, bounce_message_id, original_message_id,
                        recipient_email, professor_id, category, action, status_code,
                        diagnostic_code, remote_mta, reporting_mta, bounce_received_at, reconciled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    dsn.folder,
                    dsn.uid,
                    dsn.bounce_message_id,
                    dsn.orig_message_id,
                    dsn.recipient_email,
                    prof_id,
                    category,
                    dsn.action,
                    dsn.status_code,
                    dsn.diagnostic_code,
                    dsn.remote_mta,
                    dsn.reporting_mta,
                    dsn.bounce_date,
                    now_iso,
                ))

                # 2. Update SQLite professors and outreach_queue
                if prof_id and category in ("BOUNCED_INVALID_ADDRESS", "BOUNCED_PERMANENT_OTHER"):
                    diagnostic_summary = f"[{category}] {dsn.status_code or ''} {dsn.diagnostic_code or ''}".strip()
                    if len(diagnostic_summary) > 250:
                        diagnostic_summary = diagnostic_summary[:247] + "..."

                    if category == "BOUNCED_INVALID_ADDRESS":
                        cur.execute("""
                            UPDATE professors
                            SET outreach_status = ?,
                                email_verification_status = 'INVALID',
                                notes = CASE 
                                    WHEN notes IS NULL OR notes = '' THEN ? 
                                    ELSE notes || ' | ' || ? 
                                END,
                                updated_at = ?
                            WHERE professor_id = ?;
                        """, (category, diagnostic_summary, diagnostic_summary, now_iso, prof_id))
                    else:
                        # BOUNCED_PERMANENT_OTHER: Preserve existing email verification status!
                        cur.execute("""
                            UPDATE professors
                            SET outreach_status = ?,
                                notes = CASE 
                                    WHEN notes IS NULL OR notes = '' THEN ? 
                                    ELSE notes || ' | ' || ? 
                                END,
                                updated_at = ?
                            WHERE professor_id = ?;
                        """, (category, diagnostic_summary, diagnostic_summary, now_iso, prof_id))

                    cur.execute("""
                        UPDATE outreach_queue
                        SET status = ?,
                            error_message = ?,
                            last_attempt_at = ?
                        WHERE professor_id = ?;
                    """, (category, diagnostic_summary, now_iso, prof_id))
                    stats["sqlite_updated"] += 1

                elif prof_id and category == "TEMPORARY_DELIVERY_FAILURE":
                    diag_note = f"[TEMPORARY_DELIVERY_FAILURE] {dsn.diagnostic_code or 'Delivery delayed'}"
                    cur.execute("""
                        UPDATE professors
                        SET notes = CASE 
                            WHEN notes IS NULL OR notes = '' THEN ? 
                            ELSE notes || ' | ' || ? 
                        END,
                        updated_at = ?
                        WHERE professor_id = ?;
                    """, (diag_note, diag_note, now_iso, prof_id))

                stats["reconciled_details"].append({
                    "uid": dsn.uid,
                    "recipient": dsn.recipient_email,
                    "professor_id": prof_id,
                    "professor_name": prof_name,
                    "category": category,
                    "diagnostic": dsn.diagnostic_code,
                    "matched": bool(prof),
                })

            conn.commit()

        # 3. Synchronize Excel Workbook
        self._sync_excel_reconciliation(deduped_dsns)
        stats["excel_updated"] = stats["bounced_invalid_address"] + stats["bounced_permanent_other"]

        audit_logger.log_event(
            event_type="BOUNCE_RECONCILIATION_COMPLETED",
            actor="SYSTEM_RECONCILER",
            entity_type="CAMPAIGN",
            entity_id="SUMMER_2027",
            details={
                "inspected": stats["total_inspected"],
                "invalid_address": stats["bounced_invalid_address"],
                "permanent_other": stats["bounced_permanent_other"],
                "temporary": stats["temporary_delivery_failure"],
            },
        )
        return stats

    def _sync_excel_reconciliation(
        self, deduped_dsns: Dict[str, DeliveryStatusNotification]
    ) -> None:
        """Atomically update both sheets in the authoritative Excel workbook."""
        if not self.workbook_path.exists():
            logger.warning(f"Authoritative workbook not found at: {self.workbook_path}")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            self.sync.acquire_lock(timeout_seconds=15)
        except WorkbookLockError as e:
            logger.error(f"Cannot acquire workbook lock for bounce reconciliation: {e}")
            return

        try:
            self.sync.create_backup()
            wb = openpyxl.load_workbook(str(self.workbook_path))

            # Build fast lookup map: lowercase email -> DSN
            lookup: Dict[str, DeliveryStatusNotification] = {
                d.recipient_email.lower(): d for d in deduped_dsns.values() if d.recipient_email
            }

            # 1. Update 'Time Blocks & Distribution'
            tb_sheet_name = next(
                (s for s in wb.sheetnames if "time blocks" in s.lower()), None
            )
            if tb_sheet_name:
                ws_tb = wb[tb_sheet_name]
                for r_idx in range(1, ws_tb.max_row + 1):
                    # Column 8 is Official Institutional Email
                    cell_val = ws_tb.cell(r_idx, 8).value
                    if not cell_val:
                        continue
                    email_str = str(cell_val).strip().lower()
                    if email_str in lookup:
                        dsn = lookup[email_str]
                        category = dsn.category
                        diag_note = f"[{category}] {dsn.status_code or ''} {dsn.diagnostic_code or ''}".strip()
                        if len(diag_note) > 150:
                            diag_note = diag_note[:147] + "..."

                        if category == "BOUNCED_INVALID_ADDRESS":
                            # Col 9: Email Verification Status -> INVALID
                            ws_tb.cell(r_idx, 9).value = "INVALID"
                            # Col 25: Outreach Eligibility -> BOUNCED_INVALID_ADDRESS
                            ws_tb.cell(r_idx, 25).value = "BOUNCED_INVALID_ADDRESS"
                            # Col 29: Outreach Status -> BOUNCED_INVALID_ADDRESS
                            ws_tb.cell(r_idx, 29).value = "BOUNCED_INVALID_ADDRESS"
                            # Col 31: Message-ID / Error details
                            existing_mid = str(ws_tb.cell(r_idx, 31).value or "").strip()
                            ws_tb.cell(r_idx, 31).value = f"{existing_mid} | [BOUNCED_INVALID_ADDRESS]" if existing_mid else "[BOUNCED_INVALID_ADDRESS]"
                            # Col 18: Notes / Unresolved Issues
                            existing_notes = str(ws_tb.cell(r_idx, 18).value or "").strip()
                            ws_tb.cell(r_idx, 18).value = f"{existing_notes} | {diag_note}" if existing_notes and existing_notes != "None" else diag_note

                        elif category == "BOUNCED_PERMANENT_OTHER":
                            # Col 25: Outreach Eligibility -> BOUNCED_PERMANENT_OTHER
                            ws_tb.cell(r_idx, 25).value = "BOUNCED_PERMANENT_OTHER"
                            # Col 29: Outreach Status -> BOUNCED_PERMANENT_OTHER
                            ws_tb.cell(r_idx, 29).value = "BOUNCED_PERMANENT_OTHER"
                            # Col 31: Message-ID / Error details
                            existing_mid = str(ws_tb.cell(r_idx, 31).value or "").strip()
                            ws_tb.cell(r_idx, 31).value = f"{existing_mid} | [BOUNCED_PERMANENT_OTHER]" if existing_mid else "[BOUNCED_PERMANENT_OTHER]"
                            # Col 18: Notes / Unresolved Issues
                            existing_notes = str(ws_tb.cell(r_idx, 18).value or "").strip()
                            ws_tb.cell(r_idx, 18).value = f"{existing_notes} | {diag_note}" if existing_notes and existing_notes != "None" else diag_note

                        elif category == "TEMPORARY_DELIVERY_FAILURE":
                            # Col 18: Notes / Unresolved Issues
                            existing_notes = str(ws_tb.cell(r_idx, 18).value or "").strip()
                            temp_note = f"[DELIVERY_UNCONFIRMED] {dsn.diagnostic_code or '4.0.0 delayed'}"
                            ws_tb.cell(r_idx, 18).value = f"{existing_notes} | {temp_note}" if existing_notes and existing_notes != "None" else temp_note

            # 2. Update 'Professor Database'
            prof_sheet_name = next(
                (s for s in wb.sheetnames if "professor" in s.lower()), None
            )
            if prof_sheet_name:
                ws_prof = wb[prof_sheet_name]
                for r_idx in range(2, ws_prof.max_row + 1):
                    # Column 8 is Official Institutional Email
                    cell_val = ws_prof.cell(r_idx, 8).value
                    if not cell_val:
                        continue
                    email_str = str(cell_val).strip().lower()
                    if email_str in lookup:
                        dsn = lookup[email_str]
                        category = dsn.category
                        diag_note = f"[{category}] {dsn.status_code or ''} {dsn.diagnostic_code or ''}".strip()
                        if len(diag_note) > 150:
                            diag_note = diag_note[:147] + "..."

                        if category == "BOUNCED_INVALID_ADDRESS":
                            # Col 9: Email Verification Status -> INVALID
                            ws_prof.cell(r_idx, 9).value = "INVALID"
                            # Col 19: Outreach Status -> BOUNCED_INVALID_ADDRESS
                            ws_prof.cell(r_idx, 19).value = "BOUNCED_INVALID_ADDRESS"
                            # Col 18: Notes
                            existing_notes = str(ws_prof.cell(r_idx, 18).value or "").strip()
                            ws_prof.cell(r_idx, 18).value = f"{existing_notes} | {diag_note}" if existing_notes and existing_notes != "None" else diag_note

                        elif category == "BOUNCED_PERMANENT_OTHER":
                            # Col 19: Outreach Status -> BOUNCED_PERMANENT_OTHER
                            ws_prof.cell(r_idx, 19).value = "BOUNCED_PERMANENT_OTHER"
                            # Col 18: Notes
                            existing_notes = str(ws_prof.cell(r_idx, 18).value or "").strip()
                            ws_prof.cell(r_idx, 18).value = f"{existing_notes} | {diag_note}" if existing_notes and existing_notes != "None" else diag_note

                        elif category == "TEMPORARY_DELIVERY_FAILURE":
                            existing_notes = str(ws_prof.cell(r_idx, 18).value or "").strip()
                            temp_note = f"[DELIVERY_UNCONFIRMED] {dsn.diagnostic_code or '4.0.0 delayed'}"
                            ws_prof.cell(r_idx, 18).value = f"{existing_notes} | {temp_note}" if existing_notes and existing_notes != "None" else temp_note

            # Save workbook atomically via temp file
            temp_path = self.workbook_path.with_suffix(f".reconcile_tmp_{int(time.time()*1000)}.xlsx")
            wb.save(str(temp_path))
            wb.close()
            import shutil
            shutil.move(str(temp_path), str(self.workbook_path))
            logger.info("Excel workbook successfully synchronized with bounce reconciliation.")

        finally:
            self.sync.release_lock()

    def record_proposed_replacement(
        self,
        professor_id: str,
        professor_name: str,
        original_bounced_email: str,
        proposed_email: Optional[str],
        replacement_status: str,
        current_institution: str,
        department: str,
        source_url: str,
        evidence_notes: str,
    ) -> None:
        """
        Record a researched alternative email address in SQLite.
        Does NOT automatically retransmit; preserves evidence for verification.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO proposed_email_replacements (
                    professor_id, professor_name, original_bounced_email,
                    proposed_email, replacement_status, current_institution,
                    department, source_url, evidence_notes, researched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                professor_id,
                professor_name,
                original_bounced_email.lower(),
                proposed_email.lower() if proposed_email else None,
                replacement_status,
                current_institution,
                department,
                source_url,
                evidence_notes,
                now_iso,
            ))
            conn.commit()
