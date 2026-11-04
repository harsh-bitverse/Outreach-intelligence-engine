"""
Session 12: Real-Time Concurrent Outbound Delivery Tracker.

Tracks every outgoing professor email submitted through IIT Delhi SMTP:
- Durable professor ID, row identifier, recipient, Message-ID, SMTP acceptance timestamp
- Transport status (SMTP_ACCEPTED)
- Delivery status (UNCONFIRMED, BOUNCED_INVALID_ADDRESS, BOUNCED_PERMANENT_OTHER, TEMPORARY_DELIVERY_FAILURE, REPLIED)
- Tracking lifecycle:
  - ACTIVE_WATCH: high-frequency (60s) watch during the first 10 minutes.
  - WATCH_COMPLETE: transitions to watch complete after 10 minutes, retaining UNCONFIRMED status.
  - RESOLVED_BOUNCE: resolved upon receiving permanent failure.
  - RESOLVED_REPLY: resolved upon receiving genuine professor response.
- Fully concurrent, non-blocking, and SQLite-persisted.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.settings import settings
from src.database.connection import get_db
from src.mail.bounce_reconciler import clean_email_address
from src.security.audit_logger import audit_logger

logger = logging.getLogger(__name__)


class OutboundDeliveryTracker:
    """
    Manages concurrent 10-minute real-time delivery tracking and historical delivery states.
    Backed by the SQLite table `outbound_delivery_tracker`.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or settings.SQLITE_DB_PATH)
        self.init_database_tables()

    def init_database_tables(self) -> None:
        """Create delivery tracking table and indices if they do not exist."""
        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS outbound_delivery_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    professor_id TEXT NOT NULL,
                    university_name TEXT,
                    workbook_row_index INTEGER,
                    recipient_email TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    smtp_accepted_at TEXT NOT NULL,
                    transport_status TEXT NOT NULL DEFAULT 'SMTP_ACCEPTED',
                    delivery_status TEXT NOT NULL DEFAULT 'UNCONFIRMED',
                    tracking_status TEXT NOT NULL DEFAULT 'ACTIVE_WATCH',
                    tracking_started_at TEXT NOT NULL,
                    watch_deadline_at TEXT NOT NULL,
                    bounce_category TEXT,
                    bounce_diagnostic TEXT,
                    last_checked_at TEXT,
                    address_attempt_number INTEGER DEFAULT 1,
                    previous_attempts_history TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_odt_message_id ON outbound_delivery_tracker (message_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_odt_prof_id ON outbound_delivery_tracker (professor_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_odt_email ON outbound_delivery_tracker (recipient_email);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_odt_tracking_status ON outbound_delivery_tracker (tracking_status);")
            conn.commit()

    def track_outbound_email(
        self,
        professor_id: str,
        university_name: str,
        workbook_row_index: int,
        recipient_email: str,
        message_id: str,
        smtp_accepted_at: Optional[str] = None,
        watch_duration_minutes: int = 10,
        address_attempt_number: int = 1,
        previous_attempts_history: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a new outbound professor email immediately following SMTP acceptance.
        Non-blocking: inserts state and returns tracking metadata immediately.
        """
        now = datetime.now(timezone.utc)
        accepted_iso = smtp_accepted_at or now.isoformat()
        deadline = (now + timedelta(minutes=watch_duration_minutes)).isoformat()
        now_iso = now.isoformat()
        clean_email = clean_email_address(recipient_email)

        record = {
            "professor_id": professor_id,
            "university_name": university_name,
            "workbook_row_index": workbook_row_index,
            "recipient_email": clean_email,
            "message_id": message_id,
            "smtp_accepted_at": accepted_iso,
            "transport_status": "SMTP_ACCEPTED",
            "delivery_status": "UNCONFIRMED",
            "tracking_status": "ACTIVE_WATCH",
            "tracking_started_at": now_iso,
            "watch_deadline_at": deadline,
            "bounce_category": None,
            "bounce_diagnostic": "10-Minute Active Watch Initialized",
            "last_checked_at": now_iso,
            "address_attempt_number": address_attempt_number,
            "previous_attempts_history": previous_attempts_history,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO outbound_delivery_tracker (
                    professor_id, university_name, workbook_row_index, recipient_email,
                    message_id, smtp_accepted_at, transport_status, delivery_status,
                    tracking_status, tracking_started_at, watch_deadline_at,
                    bounce_category, bounce_diagnostic, last_checked_at,
                    address_attempt_number, previous_attempts_history, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                record["professor_id"],
                record["university_name"],
                record["workbook_row_index"],
                record["recipient_email"],
                record["message_id"],
                record["smtp_accepted_at"],
                record["transport_status"],
                record["delivery_status"],
                record["tracking_status"],
                record["tracking_started_at"],
                record["watch_deadline_at"],
                record["bounce_category"],
                record["bounce_diagnostic"],
                record["last_checked_at"],
                record["address_attempt_number"],
                record["previous_attempts_history"],
                record["created_at"],
                record["updated_at"],
            ))
            conn.commit()

        audit_logger.log_event(
            event_type="DELIVERY_TRACKER_ENROLLED",
            actor="DELIVERY_TRACKER",
            entity_type="EMAIL_MESSAGE",
            entity_id=message_id,
            details={
                "professor_id": professor_id,
                "recipient": clean_email,
                "watch_deadline": deadline
            }
        )
        return record

    def get_active_watches(self) -> List[Dict[str, Any]]:
        """Retrieve all tracked emails currently within their active watch lifecycle."""
        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, professor_id, university_name, workbook_row_index, recipient_email,
                       message_id, smtp_accepted_at, transport_status, delivery_status,
                       tracking_status, tracking_started_at, watch_deadline_at,
                       bounce_category, bounce_diagnostic, last_checked_at,
                       address_attempt_number, previous_attempts_history
                FROM outbound_delivery_tracker
                WHERE tracking_status = 'ACTIVE_WATCH';
            """)
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def get_active_watch_count(self) -> int:
        """Return the count of currently active watches."""
        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM outbound_delivery_tracker WHERE tracking_status = 'ACTIVE_WATCH';")
            return cur.fetchone()[0]

    def check_active_watches(
        self,
        new_dsns: Optional[List[Dict[str, Any]]] = None,
        new_replies: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Evaluate all ACTIVE_WATCH records against incoming DSNs, replies, and deadlines.
        Transitions expired watches to WATCH_COMPLETE (preserving UNCONFIRMED delivery status).
        Transitions bounced watches to RESOLVED_BOUNCE.
        Transitions replied watches to RESOLVED_REPLY.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        active = self.get_active_watches()

        dsns = new_dsns or []
        replies = new_replies or []

        # Index incoming DSNs by message_id and recipient
        dsn_by_msg_id: Dict[str, Dict[str, Any]] = {}
        dsn_by_email: Dict[str, Dict[str, Any]] = {}
        for d in dsns:
            mid = (d.get("orig_message_id") or "").strip()
            if mid:
                dsn_by_msg_id[mid] = d
            recip = clean_email_address(d.get("recipient_email"))
            if recip:
                dsn_by_email[recip] = d

        # Index replies by sender email and in-reply-to
        reply_by_email: Dict[str, Dict[str, Any]] = {}
        reply_by_in_reply_to: Dict[str, Dict[str, Any]] = {}
        for r in replies:
            frm = clean_email_address(r.get("from_email"))
            if frm:
                reply_by_email[frm] = r
            irt = (r.get("in_reply_to") or "").strip()
            if irt:
                reply_by_in_reply_to[irt] = r

        resolved_bounces = 0
        resolved_replies = 0
        watch_completed = 0
        still_watching = 0

        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            for item in active:
                rec_id = item["id"]
                mid = item["message_id"]
                email_addr = item["recipient_email"]
                deadline_str = item["watch_deadline_at"]
                prof_id = item["professor_id"]

                matched_dsn = dsn_by_msg_id.get(mid) or dsn_by_email.get(email_addr)
                matched_reply = reply_by_in_reply_to.get(mid) or reply_by_email.get(email_addr)

                if matched_dsn:
                    cat = matched_dsn.get("category", "BOUNCED_INVALID_ADDRESS")
                    diag = matched_dsn.get("diagnostic_code") or matched_dsn.get("status_code") or "DSN failure"

                    if cat in ("BOUNCED_INVALID_ADDRESS", "BOUNCED_PERMANENT_OTHER"):
                        cur.execute("""
                            UPDATE outbound_delivery_tracker
                            SET delivery_status = ?,
                                tracking_status = 'RESOLVED_BOUNCE',
                                bounce_category = ?,
                                bounce_diagnostic = ?,
                                last_checked_at = ?,
                                updated_at = ?
                            WHERE id = ?;
                        """, (cat, cat, diag, now_iso, now_iso, rec_id))
                        resolved_bounces += 1
                        continue
                    elif cat == "TEMPORARY_DELIVERY_FAILURE":
                        cur.execute("""
                            UPDATE outbound_delivery_tracker
                            SET delivery_status = 'TEMPORARY_DELIVERY_FAILURE',
                                bounce_category = 'TEMPORARY_DELIVERY_FAILURE',
                                bounce_diagnostic = ?,
                                last_checked_at = ?,
                                updated_at = ?
                            WHERE id = ?;
                        """, (diag, now_iso, now_iso, rec_id))
                        # Remains in active watch until deadline
                        still_watching += 1
                        continue

                if matched_reply:
                    cur.execute("""
                        UPDATE outbound_delivery_tracker
                        SET delivery_status = 'REPLIED',
                            tracking_status = 'RESOLVED_REPLY',
                            bounce_diagnostic = 'Genuine Professor Reply Confirmed',
                            last_checked_at = ?,
                            updated_at = ?
                        WHERE id = ?;
                    """, (now_iso, now_iso, rec_id))
                    resolved_replies += 1
                    continue

                # Check 10-minute deadline
                try:
                    deadline_dt = datetime.fromisoformat(deadline_str)
                    if deadline_dt.tzinfo is None:
                        deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    deadline_dt = now

                if now >= deadline_dt:
                    # 10 minutes elapsed without bounce: WATCH_COMPLETE, UNCONFIRMED
                    cur.execute("""
                        UPDATE outbound_delivery_tracker
                        SET tracking_status = 'WATCH_COMPLETE',
                            delivery_status = 'UNCONFIRMED',
                            bounce_diagnostic = 'SMTP_ACCEPTED | 10-Min Watch Complete (Unconfirmed)',
                            last_checked_at = ?,
                            updated_at = ?
                        WHERE id = ?;
                    """, (now_iso, now_iso, rec_id))
                    watch_completed += 1
                else:
                    cur.execute("""
                        UPDATE outbound_delivery_tracker
                        SET last_checked_at = ?,
                            updated_at = ?
                        WHERE id = ?;
                    """, (now_iso, now_iso, rec_id))
                    still_watching += 1

            conn.commit()

        return {
            "total_active_evaluated": len(active),
            "resolved_bounces": resolved_bounces,
            "resolved_replies": resolved_replies,
            "watch_completed": watch_completed,
            "still_watching": still_watching,
            "checked_at": now_iso
        }

    def backfill_from_historical_ledger(self) -> int:
        """
        Backfill `outbound_delivery_tracker` for all historical transmissions
        recorded in `outreach_queue`, `delivery_status_ledger`, and `professors`.
        Ensures complete chronological auditing across all campaign runs.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            # Fetch all sent / error / bounced records across professors, queue, and drafts
            cur.execute("""
                SELECT p.professor_id, p.university_name, p.full_name, p.email,
                       COALESCE(q.last_attempt_at, d.dispatched_at, p.updated_at) AS attempt_at,
                       p.outreach_status, q.error_message
                FROM professors p
                LEFT JOIN outreach_queue q ON p.professor_id = q.professor_id
                LEFT JOIN outreach_drafts d ON p.professor_id = d.professor_id AND d.status = 'SENT'
                WHERE p.outreach_status NOT IN ('AWAITING_DRAFT', 'SUPPRESSED')
                   OR q.status IN ('SENT', 'BOUNCED_INVALID_ADDRESS', 'BOUNCED_PERMANENT_OTHER', 'ERROR')
                   OR q.dispatched_at IS NOT NULL;
            """)
            queue_items = cur.fetchall()

            # Map existing tracker records by professor_id and email
            cur.execute("SELECT professor_id, recipient_email FROM outbound_delivery_tracker;")
            existing_tracked = {(r[0], r[1].lower().strip()) for r in cur.fetchall()}

            # Map bounces from delivery_status_ledger
            cur.execute("SELECT professor_id, recipient_email, category, diagnostic_code, original_message_id FROM delivery_status_ledger;")
            ledger_bounces: Dict[Tuple[str, str], Tuple[str, str, str]] = {}
            for row in cur.fetchall():
                p_id = row[0] or ""
                em = clean_email_address(row[1])
                ledger_bounces[(p_id, em)] = (row[2], row[3] or "", row[4] or "")

            inserted = 0
            for item in queue_items:
                prof_id = item[0]
                uni = item[1] or ""
                p_email = clean_email_address(item[3])
                attempt_at = item[4] or now_iso
                status = item[5]
                err = item[6] or ""

                if (prof_id, p_email) in existing_tracked:
                    continue

                # Determine delivery status and tracking status
                bounce_info = ledger_bounces.get((prof_id, p_email))
                mid = ""
                if bounce_info and bounce_info[2]:
                    mid = bounce_info[2]
                else:
                    mid = f"<historical_{prof_id}_{abs(hash(p_email))}@iitd.ac.in>"

                deliv_status = "UNCONFIRMED"
                track_status = "WATCH_COMPLETE"
                b_cat = None
                b_diag = "Historical Submission"

                if status == "BOUNCED_INVALID_ADDRESS" or (bounce_info and bounce_info[0] == "BOUNCED_INVALID_ADDRESS"):
                    deliv_status = "BOUNCED_INVALID_ADDRESS"
                    track_status = "RESOLVED_BOUNCE"
                    b_cat = "BOUNCED_INVALID_ADDRESS"
                    b_diag = bounce_info[1] if bounce_info else "Invalid recipient address"
                elif status == "BOUNCED_PERMANENT_OTHER" or (bounce_info and bounce_info[0] == "BOUNCED_PERMANENT_OTHER"):
                    deliv_status = "BOUNCED_PERMANENT_OTHER"
                    track_status = "RESOLVED_BOUNCE"
                    b_cat = "BOUNCED_PERMANENT_OTHER"
                    b_diag = bounce_info[1] if bounce_info else "Permanent delivery failure"
                elif status == "SENT":
                    deliv_status = "UNCONFIRMED"
                    track_status = "WATCH_COMPLETE"
                    b_diag = "SMTP_ACCEPTED | Historical Delivery Unconfirmed"

                deadline = attempt_at

                cur.execute("""
                    INSERT OR IGNORE INTO outbound_delivery_tracker (
                        professor_id, university_name, workbook_row_index, recipient_email,
                        message_id, smtp_accepted_at, transport_status, delivery_status,
                        tracking_status, tracking_started_at, watch_deadline_at,
                        bounce_category, bounce_diagnostic, last_checked_at,
                        address_attempt_number, previous_attempts_history, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'SMTP_ACCEPTED', ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?);
                """, (
                    prof_id, uni, 0, p_email, mid, attempt_at,
                    deliv_status, track_status, attempt_at, deadline,
                    b_cat, b_diag, now_iso, attempt_at, now_iso
                ))
                inserted += 1
                existing_tracked.add((prof_id, p_email))

            conn.commit()
            logger.info(f"Backfilled {inserted} historical records into outbound_delivery_tracker.")
            return inserted

    def get_delivery_summary(self) -> Dict[str, Any]:
        """Produce an aggregated delivery and watch status summary."""
        with get_db(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT tracking_status, COUNT(*) FROM outbound_delivery_tracker GROUP BY tracking_status;")
            by_tracking = dict(cur.fetchall())

            cur.execute("SELECT delivery_status, COUNT(*) FROM outbound_delivery_tracker GROUP BY delivery_status;")
            by_delivery = dict(cur.fetchall())

            cur.execute("SELECT COUNT(*) FROM outbound_delivery_tracker;")
            total = cur.fetchone()[0]

            return {
                "total_tracked": total,
                "by_tracking_status": by_tracking,
                "by_delivery_status": by_delivery,
            }
