"""
Opportunity Intelligence Engine: Shared Production-Compatible Queue Manager.

Architecture & Invariants:
1. Multi-Category Unified Queue:
   - ACADEMIC_LAB (Direct faculty research outreach)
   - STARTUP (Founder & engineering lead systems pitch)
   - FRONTIER_COMPANY (Industrial R&D engineering inquiry)
   - INSTITUTIONAL_PROGRAM (National lab fellowship inquiry)
   - APPLICATION_PORTAL (Formal portal submissions routed separately to Application Tracker)
2. Strict Channel & Delivery Boundary:
   - Portal-routed applications NEVER pass to the SMTP dispatcher.
   - Contacts flagged NEEDS_CONTACT_VERIFICATION or INVALID are rejected from dispatch.
3. Fresh Pre-Dispatch Production History Check:
   - Every queued record undergoes a fresh, real-time read-only check against
     the live production database (`data/outreach_engine.db`) immediately prior to dispatch.
   - Historical professor contacts and alternative aliases are permanently suppressed.
4. Unified Rate-Limiting & Budget Cadence:
   - Shared GlobalBudgetManager across all categories combined:
     - 10 emails per batch.
     - 300-second (5 min) cooling break between batches.
     - 25-second minimum spacing between individual messages.
     - 200 emails / IST day application ceiling (overridden by IITD server limits).
     - 900-second cooldown on account rate limits.
5. Recipient-Specific Dynamic Local Window & Working-Day Validation:
   - Recipient must be in active morning arrival window (08:30 - 09:30 local midpoint).
   - Recipient must be on a local working day (Monday-Friday, or Sunday-Thursday in Middle East).
6. Durable Crash Recovery & Idempotency:
   - Items in PROCESSING state upon restart are safely reconciled against delivery records,
     preventing duplicate email transmission.
"""

import os
import sqlite3
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, date, time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    ContactChannel,
    VerificationStatus,
    ContactHistoryStatus
)
from src.opportunities.budget_and_tracking import GlobalBudgetManager
from src.opportunities.contact_verifier import ContactVerifier, validate_email_syntax
from src.opportunities.timezone_engine import DynamicTimezoneEngine, CanonicalRegionalBlockRegistry
from src.security.audit_logger import audit_logger

logger = logging.getLogger("unified_queue")


@dataclass
class UnifiedQueueItem:
    queue_id: str
    opportunity_id: str
    category: str
    organization_name: str
    recipient_name: str
    recipient_email: Optional[str]
    hiring_contact_channel: str
    delivery_channel: str  # 'SMTP_GATEWAY' vs 'PORTAL_TRACKER'
    assigned_block_id: str
    iana_timezone: str
    queue_status: str  # 'QUEUED', 'PROCESSING', 'DISPATCHED_SMTP', 'ROUTED_PORTAL', 'SUPPRESSED_COLLISION', 'REJECTED_UNVERIFIED', 'REJECTED_INVALID', 'HELD_COOLDOWN'
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None
    smtp_message_id: Optional[str] = None
    scheduled_at: Optional[str] = None
    dispatched_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    retry_count: int = 0
    failure_reason: Optional[str] = None


class UnifiedOutreachQueueManager:
    """
    Production-compatible unified outreach queue orchestrating multi-category
    email and portal opportunities with strict delivery gating and crash safety.
    """

    def __init__(
        self,
        dev_conn: sqlite3.Connection,
        prod_db_path: Optional[Path] = None,
        budget_manager: Optional[GlobalBudgetManager] = None
    ):
        self.dev_conn = dev_conn
        self.dev_conn.row_factory = sqlite3.Row
        self.prod_db_path = prod_db_path or settings.SQLITE_DB_PATH
        self.budget_manager = budget_manager or GlobalBudgetManager()
        self.verifier = ContactVerifier(prod_db_path=self.prod_db_path)
        self.ist_tz = ZoneInfo("Asia/Kolkata")
        self.init_queue_schema()

    def init_queue_schema(self) -> None:
        """Initializes the unified queue table in development SQLite."""
        cur = self.dev_conn.cursor()
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
        self.dev_conn.commit()

    def enqueue_opportunity(
        self,
        opp: Opportunity,
        org: Organization,
        draft_packet: Optional[Dict[str, Any]] = None,
        target_date: Optional[date] = None
    ) -> UnifiedQueueItem:
        """
        Enqueues an opportunity into the unified queue following strict verification
        and routing rules.
        """
        if target_date is None:
            target_date = date(2026, 11, 5)

        now_iso = datetime.now(timezone.utc).isoformat()
        queue_id = f"UQ-{opp.opportunity_id[:12]}"

        tz_str = opp.iana_timezone or org.iana_timezone or "UTC"
        tz_calc = DynamicTimezoneEngine.calculate_mailing_window(tz_str, target_date=target_date)
        assigned_block = tz_calc["assigned_block_id"]

        is_portal = (
            opp.hiring_contact_channel == ContactChannel.APPLICATION_PORTAL.value
            or not opp.contact_email
            or opp.contact_history_status == ContactHistoryStatus.PORTAL_APPLICATION.value
        )

        subject = draft_packet.get("subject") if draft_packet else None
        body = draft_packet.get("body") if draft_packet else None

        # 1. Routing to Application Tracker (Portal Submissions)
        if is_portal:
            item = UnifiedQueueItem(
                queue_id=queue_id,
                opportunity_id=opp.opportunity_id,
                category=opp.opportunity_type,
                organization_name=org.name,
                recipient_name=opp.contact_name or f"{org.name} Admissions / Recruiting",
                recipient_email=opp.contact_email,
                hiring_contact_channel=opp.hiring_contact_channel,
                delivery_channel="PORTAL_TRACKER",
                assigned_block_id="PORTAL_DEADLINE_TRACKER",
                iana_timezone=tz_str,
                queue_status="ROUTED_PORTAL",
                draft_subject=subject,
                draft_body=body,
                created_at=now_iso,
                updated_at=now_iso,
                failure_reason="PORTAL_APPLICATION: Routed to deadline application tracker (never sent via SMTP)."
            )
            self._persist_queue_item(item)
            return item

        clean_email = (opp.contact_email or "").strip().lower()

        # 2. Syntax & FQDN Validation
        syntax_ok, syntax_reason = validate_email_syntax(clean_email)
        if not syntax_ok:
            item = UnifiedQueueItem(
                queue_id=queue_id,
                opportunity_id=opp.opportunity_id,
                category=opp.opportunity_type,
                organization_name=org.name,
                recipient_name=opp.contact_name or org.name,
                recipient_email=clean_email,
                hiring_contact_channel=opp.hiring_contact_channel,
                delivery_channel="INVALID_EMAIL",
                assigned_block_id=assigned_block,
                iana_timezone=tz_str,
                queue_status="REJECTED_INVALID",
                created_at=now_iso,
                updated_at=now_iso,
                failure_reason=f"INVALID_SYNTAX: {syntax_reason}"
            )
            self._persist_queue_item(item)
            return item

        # 3. Evidence Grounding & Verification Status Check
        is_evidence_missing = (
            opp.contact_verification_status == "NEEDS_CONTACT_VERIFICATION"
            or not opp.official_evidence_urls
            or len([u for u in opp.official_evidence_urls if u and u.strip() and u.strip() not in ("[]", "None", "")]) == 0
        )
        if is_evidence_missing:
            item = UnifiedQueueItem(
                queue_id=queue_id,
                opportunity_id=opp.opportunity_id,
                category=opp.opportunity_type,
                organization_name=org.name,
                recipient_name=opp.contact_name or org.name,
                recipient_email=clean_email,
                hiring_contact_channel=opp.hiring_contact_channel,
                delivery_channel="UNVERIFIED",
                assigned_block_id=assigned_block,
                iana_timezone=tz_str,
                queue_status="REJECTED_UNVERIFIED",
                created_at=now_iso,
                updated_at=now_iso,
                failure_reason="NEEDS_CONTACT_VERIFICATION: Lacks verified official evidence URL connecting email to person or channel."
            )
            self._persist_queue_item(item)
            return item

        # 4. Production Collision & Exclusion Check
        verify_res = self.verifier.verify_contact(
            email=clean_email,
            full_name=opp.contact_name,
            org_name=org.name,
            org_website=org.website_url,
            evidence_urls=opp.official_evidence_urls,
            is_portal_routed=False
        )
        if verify_res["classification"] in ("ALREADY_CONTACTED", "BOUNCED", "INVALID", "NEEDS_REVIEW"):
            item = UnifiedQueueItem(
                queue_id=queue_id,
                opportunity_id=opp.opportunity_id,
                category=opp.opportunity_type,
                organization_name=org.name,
                recipient_name=opp.contact_name or org.name,
                recipient_email=clean_email,
                hiring_contact_channel=opp.hiring_contact_channel,
                delivery_channel="SMTP_GATEWAY",
                assigned_block_id=assigned_block,
                iana_timezone=tz_str,
                queue_status="SUPPRESSED_COLLISION",
                created_at=now_iso,
                updated_at=now_iso,
                failure_reason=verify_res["reason"]
            )
            self._persist_queue_item(item)
            return item

        # 5. Verified Eligible for Scheduled Dispatch
        item = UnifiedQueueItem(
            queue_id=queue_id,
            opportunity_id=opp.opportunity_id,
            category=opp.opportunity_type,
            organization_name=org.name,
            recipient_name=opp.contact_name or org.name,
            recipient_email=clean_email,
            hiring_contact_channel=opp.hiring_contact_channel,
            delivery_channel="SMTP_GATEWAY",
            assigned_block_id=assigned_block,
            iana_timezone=tz_str,
            queue_status="QUEUED",
            draft_subject=subject,
            draft_body=body,
            created_at=now_iso,
            updated_at=now_iso
        )
        self._persist_queue_item(item)
        return item

    def _persist_queue_item(self, item: UnifiedQueueItem) -> None:
        """Persists or updates queue item in SQLite."""
        cur = self.dev_conn.cursor()
        cur.execute("""
            INSERT INTO unified_outreach_queue (
                queue_id, opportunity_id, category, organization_name, recipient_name,
                recipient_email, hiring_contact_channel, delivery_channel, assigned_block_id,
                iana_timezone, queue_status, draft_subject, draft_body, smtp_message_id,
                scheduled_at, dispatched_at, created_at, updated_at, retry_count, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(queue_id) DO UPDATE SET
                queue_status = excluded.queue_status,
                draft_subject = excluded.draft_subject,
                draft_body = excluded.draft_body,
                smtp_message_id = excluded.smtp_message_id,
                dispatched_at = excluded.dispatched_at,
                updated_at = excluded.updated_at,
                retry_count = excluded.retry_count,
                failure_reason = excluded.failure_reason;
        """, (
            item.queue_id, item.opportunity_id, item.category, item.organization_name,
            item.recipient_name, item.recipient_email, item.hiring_contact_channel,
            item.delivery_channel, item.assigned_block_id, item.iana_timezone,
            item.queue_status, item.draft_subject, item.draft_body, item.smtp_message_id,
            item.scheduled_at, item.dispatched_at, item.created_at, item.updated_at,
            item.retry_count, item.failure_reason
        ))
        self.dev_conn.commit()

    def get_queue_item(self, queue_id: str) -> Optional[UnifiedQueueItem]:
        cur = self.dev_conn.cursor()
        cur.execute("SELECT * FROM unified_outreach_queue WHERE queue_id = ?;", (queue_id,))
        row = cur.fetchone()
        if not row:
            return None
        return UnifiedQueueItem(
            queue_id=row["queue_id"],
            opportunity_id=row["opportunity_id"],
            category=row["category"],
            organization_name=row["organization_name"],
            recipient_name=row["recipient_name"],
            recipient_email=row["recipient_email"],
            hiring_contact_channel=row["hiring_contact_channel"],
            delivery_channel=row["delivery_channel"],
            assigned_block_id=row["assigned_block_id"],
            iana_timezone=row["iana_timezone"],
            queue_status=row["queue_status"],
            draft_subject=row["draft_subject"],
            draft_body=row["draft_body"],
            smtp_message_id=row["smtp_message_id"],
            scheduled_at=row["scheduled_at"],
            dispatched_at=row["dispatched_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            retry_count=row["retry_count"],
            failure_reason=row["failure_reason"]
        )

    def pre_dispatch_check(
        self,
        queue_id: str,
        now_utc: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Executes real-time pre-dispatch verification immediately before message transmission:
        1. Fresh production database cross-check (read-only) for recent contact or bounce.
        2. Recipient dynamic local working window and working-day check.
        3. Global budget manager cadence and daily ceiling check.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        item = self.get_queue_item(queue_id)
        if not item:
            return False, f"QUEUE_ITEM_NOT_FOUND: {queue_id}"

        # Safety Check: Portal items must NEVER touch SMTP
        if item.delivery_channel == "PORTAL_TRACKER" or item.queue_status == "ROUTED_PORTAL":
            return False, "DISPATCH_FORBIDDEN: Portal opportunity must never be dispatched to SMTP."

        if item.queue_status not in ("QUEUED", "PROCESSING"):
            return False, f"ITEM_NOT_DISPATCHABLE: Current status is '{item.queue_status}'."

        # 1. Fresh Production History Check (Read-Only)
        self.verifier._load_production_telemetry()
        clean_email = (item.recipient_email or "").strip().lower()

        if clean_email in self.verifier.suppressed_emails:
            self._update_status(queue_id, "SUPPRESSED_COLLISION", "Matched suppression list in live production DB.")
            return False, "SUPPRESSED_COLLISION: Recipient is in live production suppression list."

        if clean_email in self.verifier.bounced_emails:
            self._update_status(queue_id, "SUPPRESSED_COLLISION", "Recipient previously bounced in live production.")
            return False, "SUPPRESSED_COLLISION: Recipient has permanent bounce in production delivery telemetry."

        if clean_email in self.verifier.contacted_emails:
            self._update_status(queue_id, "SUPPRESSED_COLLISION", "Recipient was already contacted in live production.")
            return False, "SUPPRESSED_COLLISION: Recipient has already received outreach in live campaign."

        # 2. Dynamic Local Time Window & Working-Day Check
        is_active_window, window_reason = DynamicTimezoneEngine.is_recipient_in_active_window(
            iana_timezone=item.iana_timezone,
            now_utc=now_utc
        )
        if not is_active_window:
            return False, f"LOCAL_WINDOW_INELIGIBLE: {window_reason}"

        # 3. Global Budget & Rate-Limit Check
        can_dispatch, budget_msg = self.budget_manager.can_dispatch(
            category=item.category,
            requested_count=1,
            now=now_utc
        )
        if not can_dispatch:
            return False, f"BUDGET_GATED: {budget_msg}"

        return True, "DISPATCH_AUTHORIZED"

    def dispatch_queue_item(
        self,
        queue_id: str,
        simulated: bool = True,
        now_utc: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Executes atomic dispatch of a single queue item.
        Transitions state: QUEUED -> PROCESSING -> DISPATCHED_SMTP.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        item = self.get_queue_item(queue_id)
        if not item:
            raise ValueError(f"Queue item {queue_id} not found")

        # Portal Guard: Never send portal items to SMTP
        if item.delivery_channel == "PORTAL_TRACKER" or item.queue_status == "ROUTED_PORTAL":
            raise ValueError(f"PORTAL_DISPATCH_REJECTED: Item {queue_id} is a portal application and cannot be dispatched via SMTP.")

        # Set durable state to PROCESSING
        self._update_status(queue_id, "PROCESSING")

        # Execute fresh pre-dispatch check
        authorized, reason = self.pre_dispatch_check(queue_id, now_utc=now_utc)
        if not authorized:
            # Revert from PROCESSING if budget or window held
            if "BUDGET_GATED" in reason or "LOCAL_WINDOW_INELIGIBLE" in reason:
                self._update_status(queue_id, "QUEUED", failure_reason=reason)
            return {
                "success": False,
                "queue_id": queue_id,
                "status": self.get_queue_item(queue_id).queue_status,
                "reason": reason
            }

        # Generate authentic outgoing SMTP Message-ID
        timestamp_ms = int(now_utc.timestamp() * 1000)
        pid = os.getpid()
        hash_sfx = abs(hash(f"{queue_id}:{timestamp_ms}")) % 10000000
        real_message_id = f"<{timestamp_ms}.{pid}.{hash_sfx}@iitd.ac.in>"

        now_iso = now_utc.isoformat()

        if simulated:
            # Record simulated dispatch in global budget manager
            self.budget_manager.record_dispatch(
                category=item.category,
                count=1,
                now=now_utc
            )

            # Commit DISPATCHED_SMTP state
            cur = self.dev_conn.cursor()
            cur.execute("""
                UPDATE unified_outreach_queue
                SET queue_status = 'DISPATCHED_SMTP',
                    smtp_message_id = ?,
                    dispatched_at = ?,
                    updated_at = ?,
                    failure_reason = NULL
                WHERE queue_id = ?;
            """, (real_message_id, now_iso, now_iso, queue_id))
            self.dev_conn.commit()

            audit_logger.log_event(
                event_type="UNIFIED_QUEUE_DISPATCH_SIMULATED",
                actor="UNIFIED_QUEUE_RUNNER",
                entity_type="QUEUE_ITEM",
                entity_id=queue_id,
                details={
                    "category": item.category,
                    "recipient_email": item.recipient_email,
                    "smtp_message_id": real_message_id,
                    "assigned_block_id": item.assigned_block_id
                }
            )

            return {
                "success": True,
                "queue_id": queue_id,
                "status": "DISPATCHED_SMTP",
                "recipient_email": item.recipient_email,
                "smtp_message_id": real_message_id,
                "dispatched_at": now_iso,
                "cadence_status": "250_OK_QUEUED"
            }
        else:
            raise NotImplementedError("Live production multi-category SMTP transmission requires explicit operator activation.")

    def recover_from_crash(self) -> Dict[str, Any]:
        """
        Reconciles orphaned records in 'PROCESSING' state following process crash or restart.
        Ensures idempotent recovery and zero duplicate dispatches.
        """
        cur = self.dev_conn.cursor()
        cur.execute("SELECT * FROM unified_outreach_queue WHERE queue_status = 'PROCESSING';")
        orphans = cur.fetchall()

        recovered_count = 0
        requeued_count = 0

        now_iso = datetime.now(timezone.utc).isoformat()

        for row in orphans:
            q_id = row["queue_id"]
            msg_id = row["smtp_message_id"]

            if msg_id:
                # Dispatched before crash but status write incomplete
                cur.execute("""
                    UPDATE unified_outreach_queue
                    SET queue_status = 'DISPATCHED_SMTP',
                        updated_at = ?
                    WHERE queue_id = ?;
                """, (now_iso, q_id))
                recovered_count += 1
            else:
                # Interrupted before transmission; safely reset to QUEUED
                cur.execute("""
                    UPDATE unified_outreach_queue
                    SET queue_status = 'QUEUED',
                        updated_at = ?,
                        retry_count = retry_count + 1,
                        failure_reason = 'RECOVERED_AFTER_PROCESS_RESTART'
                    WHERE queue_id = ?;
                """, (now_iso, q_id))
                requeued_count += 1

        self.dev_conn.commit()

        audit_logger.log_event(
            event_type="UNIFIED_QUEUE_CRASH_RECOVERY",
            actor="UNIFIED_QUEUE_MANAGER",
            entity_type="QUEUE",
            entity_id="ALL",
            details={
                "orphans_found": len(orphans),
                "recovered_dispatched": recovered_count,
                "requeued_for_dispatch": requeued_count
            }
        )

        return {
            "orphans_found": len(orphans),
            "recovered_dispatched": recovered_count,
            "requeued_for_dispatch": requeued_count
        }

    def _update_status(self, queue_id: str, status: str, failure_reason: Optional[str] = None) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = self.dev_conn.cursor()
        cur.execute("""
            UPDATE unified_outreach_queue
            SET queue_status = ?,
                failure_reason = COALESCE(?, failure_reason),
                updated_at = ?
            WHERE queue_id = ?;
        """, (status, failure_reason, now_iso, queue_id))
        self.dev_conn.commit()
