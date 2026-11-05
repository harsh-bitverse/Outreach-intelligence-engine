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
        Reads delivery tracker telemetry in READ-ONLY mode.
        Returns contacted counts, delivery success, bounces, and classified replies.
        """
        if not self.prod_db_path.exists():
            return {
                "professors_contacted": 0,
                "emails_delivered": 0,
                "emails_bounced": 0,
                "replies_received": 0,
                "classified_outcomes": {}
            }

        try:
            conn = sqlite3.connect(f"file:{self.prod_db_path}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()

            # 1. Contacted professors
            cur.execute("""
                SELECT COUNT(DISTINCT professor_id) 
                FROM outbound_delivery_tracker;
            """)
            contacted = cur.fetchone()[0] or 0

            # 2. Delivered vs Bounced
            cur.execute("""
                SELECT 
                    SUM(CASE WHEN delivery_status IN ('DELIVERED', 'UNCONFIRMED', 'REPLIED') THEN 1 ELSE 0 END) as delivered,
                    SUM(CASE WHEN delivery_status LIKE 'BOUNCED%' THEN 1 ELSE 0 END) as bounced
                FROM outbound_delivery_tracker;
            """)
            deliv_row = cur.fetchone()
            delivered = deliv_row[0] or 0
            bounced = deliv_row[1] or 0

            # 3. Classified replies from reply_classifications table
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reply_classifications';")
            replies_exist = bool(cur.fetchone())
            classified_outcomes: Dict[str, int] = {}
            total_replies = 0

            if replies_exist:
                cur.execute("""
                    SELECT category, COUNT(*) 
                    FROM reply_classifications 
                    GROUP BY category;
                """)
                for cat, count in cur.fetchall():
                    std_cat = self.REPLY_MAPPING.get(cat, AcademicReplyOutcome.NEEDS_REVIEW.value)
                    classified_outcomes[std_cat] = classified_outcomes.get(std_cat, 0) + count
                    total_replies += count

            conn.close()

            return {
                "professors_contacted": contacted,
                "emails_delivered": delivered,
                "emails_bounced": bounced,
                "replies_received": total_replies,
                "classified_outcomes": classified_outcomes
            }

        except Exception as e:
            logger.warning(f"Could not read production telemetry (safe fallback): {e}")
            return {
                "professors_contacted": 0,
                "emails_delivered": 0,
                "emails_bounced": 0,
                "replies_received": 0,
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
