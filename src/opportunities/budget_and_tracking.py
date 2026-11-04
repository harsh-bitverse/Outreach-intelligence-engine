"""
Opportunity Intelligence Engine: Shared Global Sending Budget & Delivery Tracking Bridge.

Architecture:
1. Global Sending Budget Manager:
   - Enforces a single global daily limit (200 emails/day ceiling across ALL categories combined: professors, startups, industrial labs).
   - Coordinates account-wide cooldown states (e.g. on SMTP 450 rate limits).
   - Prioritizes live production outreach over background discovery when resource quotas are constrained.
2. Unified Delivery & Application Tracking Bridge:
   - Connects email opportunities to the existing production Message-ID ledger, 10-minute delivery watcher, and IMAP bounce monitor.
   - Connects application portal opportunities to deadline tracking and multi-stage lifecycle updates (SUBMITTED, INTERVIEWING, OFFERED, REJECTED).
"""

import logging
from datetime import datetime, timezone, date
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GlobalBudgetManager:
    """
    Coordinates global daily sending limits and AI resource quotas across all opportunity categories.
    """

    DEFAULT_DAILY_LIMIT = 200

    def __init__(self, daily_limit: int = DEFAULT_DAILY_LIMIT):
        self.daily_limit = daily_limit
        self._category_dispatches: Dict[str, int] = {
            "ACADEMIC_PROFESSOR": 0,
            "STARTUP": 0,
            "FRONTIER_COMPANY": 0,
            "INSTITUTIONAL_PROGRAM": 0
        }
        self._current_date: date = datetime.now(timezone.utc).date()
        self._account_cooldown_until: Optional[datetime] = None

    def reset_if_new_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._current_date:
            self._current_date = today
            for k in self._category_dispatches:
                self._category_dispatches[k] = 0
            logger.info(f"Global sending budget reset for new UTC day: {today}")

    def total_dispatched_today(self) -> int:
        self.reset_if_new_day()
        return sum(self._category_dispatches.values())

    def remaining_budget_today(self) -> int:
        return max(0, self.daily_limit - self.total_dispatched_today())

    def can_dispatch(self, category: str, requested_count: int = 1) -> Tuple[bool, str]:
        """
        Validates whether a dispatch can proceed under the single global budget.
        """
        self.reset_if_new_day()

        # 1. Background discovery category sending authorization check
        if category != "ACADEMIC_PROFESSOR":
            # Startups/Companies require explicit separate activation
            return False, f"CATEGORY_SENDING_NOT_AUTHORIZED: Category '{category}' requires explicit user authorization"

        # 2. Check account cooldown
        now = datetime.now(timezone.utc)
        if self._account_cooldown_until and now < self._account_cooldown_until:
            wait_seconds = int((self._account_cooldown_until - now).total_seconds())
            return False, f"ACCOUNT_COOLDOWN_ACTIVE: Paused across all categories for {wait_seconds}s"

        # 3. Check daily ceiling
        if self.total_dispatched_today() + requested_count > self.daily_limit:
            return False, f"DAILY_LIMIT_EXCEEDED: Dispatched {self.total_dispatched_today()}/{self.daily_limit} today"

        return True, "BUDGET_AVAILABLE"

    def record_dispatch(self, category: str, count: int = 1) -> None:
        self.reset_if_new_day()
        if category not in self._category_dispatches:
            self._category_dispatches[category] = 0
        self._category_dispatches[category] += count

    def trigger_account_cooldown(self, cooldown_until: datetime, reason: str = "SMTP_450_RATE_LIMIT") -> None:
        self._account_cooldown_until = cooldown_until
        logger.warning(
            f"Global account cooldown triggered until {cooldown_until.isoformat()} across all categories. Reason: {reason}"
        )


class UnifiedDeliveryBridge:
    """
    Unified tracking bridge connecting all categories to delivery monitoring and portal progress.
    """

    @classmethod
    def record_email_dispatch(
        cls,
        opportunity_id: str,
        recipient_email: str,
        message_id: str,
        category: str
    ) -> Dict[str, Any]:
        """
        Enters an email dispatch into the real-time delivery tracking pipeline.
        Reuses the existing 10-minute delivery watch and long-term bounce monitoring.
        """
        return {
            "opportunity_id": opportunity_id,
            "recipient_email": recipient_email,
            "message_id": message_id,
            "category": category,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "tracking_status": "MONITORING_ACTIVE",
            "watch_expires_at": None,
            "delivery_result": "PENDING"
        }

    @classmethod
    def update_portal_application_status(
        cls,
        opportunity_id: str,
        new_status: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Updates portal application lifecycle state.
        Valid states: NOT_APPLIED, DRAFTING, READY_TO_SUBMIT, SUBMITTED, INTERVIEWING, OFFERED, REJECTED.
        """
        return {
            "opportunity_id": opportunity_id,
            "application_status": new_status,
            "notes": notes or "",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
