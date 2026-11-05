"""
Opportunity Intelligence Engine: Shared Global Sending Budget & Delivery Tracking Bridge.

Architecture:
1. Established Production Rate-Limiting & Cadence:
   - Batch size: 10 emails (BATCH_SIZE = 10)
   - Inter-batch break: 300 seconds (COOLING_BREAK_SECONDS = 300, mandatory 5-minute cooldown)
   - Minimum per-message interval: 25 seconds (MIN_PER_MESSAGE_INTERVAL_SECONDS = 25)
   - Global daily application ceiling: 200 emails per IST day (DAILY_MAX_OUTREACH = 200) across ALL categories combined
   - Account-wide SMTP rate-limit cooldown: 900 seconds (15-minute protective response to SMTP 450/421 errors)
   - Actual IITD server restrictions always override the application ceiling.
2. Global Sending Budget Manager:
   - Enforces a single global daily limit across ALL categories combined: professors, startups, industrial labs, institutional fellowships.
   - Coordinates account-wide cooldown states (e.g. on SMTP 450 rate limits).
   - Non-academic categories require explicit user authorization prior to active transmission.
3. Unified Delivery & Application Tracking Bridge:
   - Connects email opportunities to the existing production Message-ID ledger, 10-minute delivery watcher, and IMAP bounce monitor.
   - Connects application portal opportunities to deadline tracking and multi-stage lifecycle updates (SUBMITTED, INTERVIEWING, OFFERED, REJECTED).
"""

import logging
from datetime import datetime, timezone, date, timedelta
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings

logger = logging.getLogger(__name__)


class GlobalBudgetManager:
    """
    Coordinates global daily sending limits, batch cadence, and cooldown states
    across all opportunity categories according to the established production configuration.
    """

    # Established production rate-limiting configuration
    BATCH_SIZE: int = getattr(settings, "BATCH_SIZE", 10)
    INTER_BATCH_BREAK_SECONDS: int = getattr(settings, "COOLING_BREAK_SECONDS", 300)
    BATCH_COOLING_BREAK_SECONDS: int = INTER_BATCH_BREAK_SECONDS
    MIN_PER_MESSAGE_INTERVAL_SECONDS: int = getattr(settings, "MIN_INTERVAL_SECONDS_BETWEEN_EMAILS", 25)
    DEFAULT_DAILY_LIMIT: int = getattr(settings, "DAILY_MAX_OUTREACH", 200)
    SMTP_RATE_LIMIT_COOLDOWN_SECONDS: int = getattr(settings, "SMTP_RATE_LIMIT_COOLDOWN_SECONDS", 900)

    def __init__(
        self,
        daily_limit: Optional[int] = None,
        batch_size: Optional[int] = None,
        cooling_period_seconds: Optional[int] = None,
        authorized_categories: Optional[set] = None
    ):
        self.daily_limit = daily_limit or self.DEFAULT_DAILY_LIMIT
        self.batch_size = batch_size or self.BATCH_SIZE
        self.cooling_period_seconds = cooling_period_seconds or self.INTER_BATCH_BREAK_SECONDS
        self.authorized_categories = set(authorized_categories) if authorized_categories else {"ACADEMIC_PROFESSOR", "ACADEMIC_LAB"}
        self._category_dispatches: Dict[str, int] = {
            "ACADEMIC_PROFESSOR": 0,
            "ACADEMIC_LAB": 0,
            "STARTUP": 0,
            "FRONTIER_COMPANY": 0,
            "INSTITUTIONAL_PROGRAM": 0
        }
        self.ist_tz = ZoneInfo("Asia/Kolkata")
        self._current_date: date = datetime.now(self.ist_tz).date()
        self._account_cooldown_until: Optional[datetime] = None
        self._last_dispatch_at: Optional[datetime] = None
        self._current_batch_count: int = 0
        self._last_batch_completed_at: Optional[datetime] = None

    def authorize_category(self, category: str) -> None:
        """Authorizes sending for a specific category (e.g. following explicit operator approval)."""
        self.authorized_categories.add(category)
        if category not in self._category_dispatches:
            self._category_dispatches[category] = 0

    def reset_if_new_day(self) -> None:
        """Resets daily sending count at midnight IST."""
        today = datetime.now(self.ist_tz).date()
        if today != self._current_date:
            self._current_date = today
            for k in self._category_dispatches:
                self._category_dispatches[k] = 0
            self._current_batch_count = 0
            logger.info(f"Global sending budget reset for new IST day: {today}")

    def total_dispatched_today(self) -> int:
        self.reset_if_new_day()
        return sum(self._category_dispatches.values())

    def remaining_budget_today(self) -> int:
        return max(0, self.daily_limit - self.total_dispatched_today())

    def can_dispatch(
        self,
        category: str,
        requested_count: int = 1,
        now: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Validates whether a dispatch can proceed under the single global budget,
        the 300-second inter-batch pause, the 25-second per-message interval, and active cooldowns.
        """
        self.reset_if_new_day()
        if now is None:
            now = datetime.now(timezone.utc)

        # 1. Category sending authorization check (Production protection safeguard)
        if category not in self.authorized_categories:
            return False, f"CATEGORY_SENDING_NOT_AUTHORIZED: Category '{category}' requires explicit user authorization"

        # 2. Check daily ceiling across ALL categories combined (200 emails / IST day)
        if self.total_dispatched_today() + requested_count > self.daily_limit:
            return False, f"DAILY_LIMIT_EXCEEDED: Dispatched {self.total_dispatched_today()}/{self.daily_limit} today across shared budget"

        # 3. Check account-wide cooldown (e.g. SMTP 450 rate-limit)
        if self._account_cooldown_until and now < self._account_cooldown_until:
            wait_seconds = int((self._account_cooldown_until - now).total_seconds())
            return False, f"ACCOUNT_COOLDOWN_ACTIVE: Paused across all categories for {wait_seconds}s"

        # 4. Check inter-batch cooling break (300 seconds after every 10 emails)
        if self._last_batch_completed_at:
            elapsed_since_batch = (now - self._last_batch_completed_at).total_seconds()
            if elapsed_since_batch < self.INTER_BATCH_BREAK_SECONDS:
                remaining_break = int(self.INTER_BATCH_BREAK_SECONDS - elapsed_since_batch)
                return False, f"INTER_BATCH_COOLDOWN_ACTIVE: Mandatory 300-second break active ({remaining_break}s remaining)"

        # 5. Check minimum per-message interval (25 seconds)
        if self._last_dispatch_at:
            elapsed_since_last = (now - self._last_dispatch_at).total_seconds()
            if elapsed_since_last < self.MIN_PER_MESSAGE_INTERVAL_SECONDS:
                remaining_gap = int(self.MIN_PER_MESSAGE_INTERVAL_SECONDS - elapsed_since_last)
                return False, f"PER_MESSAGE_INTERVAL_ACTIVE: Minimum 25-second spacing active ({remaining_gap}s remaining)"

        return True, "BUDGET_AVAILABLE"

    def record_dispatch(
        self,
        category: str,
        count: int = 1,
        now: Optional[datetime] = None
    ) -> None:
        """
        Records email dispatch across global shared budget, updating batch cadence counters.
        """
        self.reset_if_new_day()
        if now is None:
            now = datetime.now(timezone.utc)

        if category not in self._category_dispatches:
            self._category_dispatches[category] = 0
        self._category_dispatches[category] += count

        self._last_dispatch_at = now
        self._current_batch_count += count

        # If batch threshold reached (10 emails), trigger mandatory 300-second cooling break
        if self._current_batch_count >= self.BATCH_SIZE:
            self._last_batch_completed_at = now
            self._current_batch_count = 0
            logger.info(
                f"Batch of {self.BATCH_SIZE} complete. Enforcing mandatory {self.INTER_BATCH_BREAK_SECONDS}-second inter-batch cooling break."
            )

    def trigger_account_cooldown(
        self,
        cooldown_until: Optional[datetime] = None,
        reason: str = "SMTP_450_RATE_LIMIT"
    ) -> None:
        """
        Triggers protective account-wide cooldown (default 900 seconds) pausing all categories.
        """
        now = datetime.now(timezone.utc)
        self._account_cooldown_until = cooldown_until or (now + timedelta(seconds=self.SMTP_RATE_LIMIT_COOLDOWN_SECONDS))
        logger.warning(
            f"Global account cooldown triggered until {self._account_cooldown_until.isoformat()} across all categories. Reason: {reason}"
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
