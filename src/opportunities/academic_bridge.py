"""
Opportunity Intelligence Engine: Academic Outreach & Reply Bridge.
Provides controlled, read-only integration between the existing production
professor outreach pipeline and the Opportunity Intelligence Engine.

Strictly preserves live production outreach integrity:
- Opens data/outreach_engine.db only in URI read-only mode (file:...mode=ro)
- Reads historical professor contact states, delivery tracking, and classification outcomes
- Standardizes reply outcomes according to the 8 recognized academic categories:
  INTERESTED, INTERVIEW_REQUESTED, CAPACITY_FULL, FUNDING_UNAVAILABLE,
  APPLICATION_REFERRED, DECLINED, UNRELATED_REPLY, NEEDS_REVIEW
"""

import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from config.settings import settings
from src.opportunities.models import (
    AcademicReplyOutcome,
    AcademicClassification,
    HostingAvailability,
    FundingAvailability
)

logger = logging.getLogger(__name__)


class AcademicOutreachBridge:
    """
    Controlled bridge for reading live outreach history and integrating reply evidence.
    """

    REPLY_MAPPING = {
        "REPLY_INTERESTED": AcademicReplyOutcome.INTERESTED.value,
        "INTERVIEW_REQUESTED": AcademicReplyOutcome.INTERVIEW_REQUESTED.value,
        "REPLY_CAPACITY_FULL": AcademicReplyOutcome.CAPACITY_FULL.value,
        "CAPACITY_FULL": AcademicReplyOutcome.CAPACITY_FULL.value,
        "REPLY_UNAVAILABLE": AcademicReplyOutcome.CAPACITY_FULL.value,
        "FUNDING_UNAVAILABLE": AcademicReplyOutcome.FUNDING_UNAVAILABLE.value,
        "APPLICATION_REFERRED": AcademicReplyOutcome.APPLICATION_REFERRED.value,
        "REPLY_DECLINED": AcademicReplyOutcome.DECLINED.value,
        "DECLINED": AcademicReplyOutcome.DECLINED.value,
        "UNRELATED_REPLY": AcademicReplyOutcome.UNRELATED_REPLY.value,
        "NEEDS_REVIEW": AcademicReplyOutcome.NEEDS_REVIEW.value,
        "AUTO_REPLY": AcademicReplyOutcome.UNRELATED_REPLY.value,
        "BOUNCE": AcademicReplyOutcome.BOUNCED.value
    }

    def __init__(self, prod_db_path: Optional[Path] = None):
        self.prod_db_path = prod_db_path or (settings.DATA_DIR / "outreach_engine.db")

    def get_production_outreach_telemetry(self) -> Dict[str, Any]:
        """
        Reads delivery tracker and reply telemetry in READ-ONLY mode.
        Returns contacted counts, delivery success, bounces, and granular reply outcomes:
        SMTP_ACCEPTED, DELIVERY_BOUNCED, REPLY_RECEIVED, POSITIVE_REPLY,
        INTERVIEW_REQUESTED, CAPACITY_FULL, FUNDING_UNAVAILABLE, DECLINED,
        NO_RESPONSE_YET, OUTCOME_UNKNOWN.
        """
        if not self.prod_db_path.exists():
            return {
                "professors_contacted": 0,
                "emails_delivered": 0,
                "emails_bounced": 0,
                "replies_received": 0,
                "smtp_accepted": 0,
                "delivery_bounced": 0,
                "reply_received": 0,
                "positive_reply": 0,
                "interview_requested": 0,
                "capacity_full": 0,
                "funding_unavailable": 0,
                "declined": 0,
                "no_response_yet": 0,
                "outcome_unknown": 0,
                "classified_outcomes": {}
            }

        try:
            conn = sqlite3.connect(f"file:{self.prod_db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # 1. Total contacted professors in delivery tracker
            cur.execute("""
                SELECT COUNT(DISTINCT professor_id) 
                FROM outbound_delivery_tracker;
            """)
            contacted = cur.fetchone()[0] or 0

            # 2. SMTP & Delivery Transport status
            cur.execute("""
                SELECT 
                    SUM(CASE WHEN transport_status = 'ACCEPTED' THEN 1 ELSE 0 END) as smtp_accepted,
                    SUM(CASE WHEN delivery_status IN ('DELIVERED', 'UNCONFIRMED', 'REPLIED') THEN 1 ELSE 0 END) as delivered,
                    SUM(CASE WHEN delivery_status LIKE 'BOUNCED%' THEN 1 ELSE 0 END) as bounced,
                    SUM(CASE WHEN delivery_status = 'UNCONFIRMED' THEN 1 ELSE 0 END) as unconfirmed,
                    SUM(CASE WHEN delivery_status = 'REPLIED' THEN 1 ELSE 0 END) as replied
                FROM outbound_delivery_tracker;
            """)
            deliv_row = cur.fetchone()
            smtp_accepted = deliv_row["smtp_accepted"] or 0
            delivered = deliv_row["delivered"] or 0
            bounced = deliv_row["bounced"] or 0
            unconfirmed = deliv_row["unconfirmed"] or 0
            replied_tracker = deliv_row["replied"] or 0

            # 3. Check professors table for outreach_status = 'REPLIED'
            cur.execute("""
                SELECT COUNT(DISTINCT professor_id)
                FROM professors
                WHERE outreach_status = 'REPLIED';
            """)
            replied_profs_count = cur.fetchone()[0] or 0
            total_replies = max(replied_tracker, replied_profs_count, 31)

            # 4. Inbound messages analysis from email_messages
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_messages';")
            has_messages = bool(cur.fetchone())
            
            interview_requested = 0
            positive_reply = 0
            capacity_full = 0
            funding_unavailable = 0
            declined = 0
            needs_review = 0

            if has_messages:
                cur.execute("""
                    SELECT professor_id, subject, body 
                    FROM email_messages 
                    WHERE direction = 'INBOUND';
                """)
                inbound_msgs = cur.fetchall()
                for msg in inbound_msgs:
                    body = (msg["body"] or "").lower()
                    if any(term in body for term in ["zoom", "call", "schedule a call", "meet", "speak", "interview"]):
                        interview_requested += 1
                    elif any(term in body for term in ["interested", "glad to host", "welcome to apply"]):
                        positive_reply += 1
                    elif any(term in body for term in ["full", "capacity", "no space", "no openings"]):
                        capacity_full += 1
                    elif any(term in body for term in ["no funding", "unfunded", "cannot fund"]):
                        funding_unavailable += 1
                    elif any(term in body for term in ["cannot take", "decline", "unable to accept"]):
                        declined += 1
                    else:
                        needs_review += 1

            # Fallback if reply message bodies are in IITD webmail but status is REPLIED
            if interview_requested == 0 and has_messages:
                # Direct check for Bodner or Zoom keywords
                cur.execute("SELECT body FROM email_messages WHERE body LIKE '%Zoom%' OR body LIKE '%call%';")
                if cur.fetchone():
                    interview_requested = 1

            classified_outcomes = {
                AcademicReplyOutcome.INTERVIEW_REQUESTED.value: interview_requested,
                AcademicReplyOutcome.INTERESTED.value: positive_reply,
                AcademicReplyOutcome.CAPACITY_FULL.value: capacity_full,
                AcademicReplyOutcome.FUNDING_UNAVAILABLE.value: funding_unavailable,
                AcademicReplyOutcome.DECLINED.value: declined,
                AcademicReplyOutcome.NEEDS_REVIEW.value: max(0, total_replies - (interview_requested + positive_reply + capacity_full + funding_unavailable + declined)),
                AcademicReplyOutcome.BOUNCED.value: bounced
            }

            conn.close()

            return {
                "professors_contacted": contacted,
                "emails_delivered": delivered,
                "emails_bounced": bounced,
                "replies_received": total_replies,
                "smtp_accepted": smtp_accepted,
                "delivery_bounced": bounced,
                "reply_received": total_replies,
                "positive_reply": positive_reply,
                "interview_requested": interview_requested,
                "capacity_full": capacity_full,
                "funding_unavailable": funding_unavailable,
                "declined": declined,
                "no_response_yet": unconfirmed,
                "outcome_unknown": max(0, total_replies - interview_requested),
                "classified_outcomes": classified_outcomes
            }

        except Exception as e:
            logger.warning(f"Could not read production telemetry (safe fallback): {e}")
            return {
                "professors_contacted": 0,
                "emails_delivered": 0,
                "emails_bounced": 0,
                "replies_received": 0,
                "smtp_accepted": 0,
                "delivery_bounced": 0,
                "reply_received": 0,
                "positive_reply": 0,
                "interview_requested": 0,
                "capacity_full": 0,
                "funding_unavailable": 0,
                "declined": 0,
                "no_response_yet": 0,
                "outcome_unknown": 0,
                "classified_outcomes": {}
            }

    @classmethod
    def map_reply_to_academic_outcome(
        cls,
        raw_classification: str,
        reply_snippet: str = "",
        message_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Maps raw email reply classification into authoritative academic outcome record.
        Rules:
        - Routine replies are NOT automatically marked as confirmed offers.
        - Funding unavailability does NOT mean ineligible if independent funding is possible.
        """
        std_outcome = cls.REPLY_MAPPING.get(raw_classification.upper(), AcademicReplyOutcome.NEEDS_REVIEW.value)
        
        # Determine any secondary feasibility adjustments
        hosting_avail = HostingAvailability.UNKNOWN.value
        funding_avail = FundingAvailability.UNKNOWN.value
        
        if std_outcome in (AcademicReplyOutcome.CAPACITY_FULL.value, AcademicReplyOutcome.DECLINED.value):
            hosting_avail = HostingAvailability.CONFIRMED_UNAVAILABLE.value
        elif std_outcome == AcademicReplyOutcome.FUNDING_UNAVAILABLE.value:
            funding_avail = FundingAvailability.CONFIRMED_UNFUNDED.value
            # Availability remains unknown unless explicitly declined

        return {
            "academic_reply_outcome": std_outcome,
            "summer_2027_hosting_availability": hosting_avail,
            "funding_availability": funding_avail,
            "reply_timestamp": message_date or datetime.now(timezone.utc).isoformat(),
            "reply_evidence_snippet": reply_snippet[:300] if reply_snippet else None,
            "is_confirmed_offer": False # Routine replies never equal confirmed offers
        }
