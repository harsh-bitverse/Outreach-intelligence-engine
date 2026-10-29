"""
Missed Scheduled Window Recovery Engine.
Detects expired dispatch windows, prevents unseasonal out-of-window email deliveries
(e.g., afternoon or late-night arrival at recipient university), and automatically
re-queues unsent outreach to the next valid weekday morning (8:00 - 9:00 AM local).
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings
from src.scheduler.timezone_engine import TimezoneEngine
from src.security.audit_logger import audit_logger


class MissedWindowRecoveryEngine:
    """Manages detection and graceful rollover of missed scheduled outreach windows."""

    def __init__(self, tz_engine: Optional[TimezoneEngine] = None):
        self.tz_engine = tz_engine or TimezoneEngine()
        self.operator_tz = ZoneInfo(settings.OPERATOR_TIMEZONE)

    def evaluate_and_recover_window(
        self,
        university_name: str,
        country: str,
        scheduled_date: date,
        current_dt_ist: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Evaluate if a scheduled morning window (8:00 - 9:00 AM local) has expired.
        If expired, rolls over to next valid business day morning.
        """
        now_ist = current_dt_ist or datetime.now(self.operator_tz)
        iana_tz_name = self.tz_engine.resolve_timezone(university_name, country)
        uni_tz = ZoneInfo(iana_tz_name)

        # 1. Calculate local morning window for scheduled date (8:00 AM to 9:00 AM local)
        local_target_start = datetime.combine(scheduled_date, datetime.min.time(), tzinfo=uni_tz).replace(hour=8, minute=0)
        local_target_end = datetime.combine(scheduled_date, datetime.min.time(), tzinfo=uni_tz).replace(hour=9, minute=0)

        ist_target_start = local_target_start.astimezone(self.operator_tz)
        ist_target_end = local_target_end.astimezone(self.operator_tz)

        # 2. Check if window is active, upcoming, or missed
        if now_ist < ist_target_start:
            window_status = "UPCOMING"
            needs_recovery = False
            recovery_details = None
        elif ist_target_start <= now_ist <= ist_target_end:
            window_status = "WINDOW_ACTIVE"
            needs_recovery = False
            recovery_details = None
        else:
            window_status = "MISSED_WINDOW_EXPIRED"
            needs_recovery = True

            # Calculate next valid business day (Monday - Friday)
            next_day = scheduled_date + timedelta(days=1)
            while next_day.weekday() >= 5: # 5=Sat, 6=Sun
                next_day += timedelta(days=1)

            # Recalculate for next business day
            recov_local = datetime.combine(next_day, datetime.min.time(), tzinfo=uni_tz).replace(hour=8, minute=0)
            recov_ist = recov_local.astimezone(self.operator_tz)

            recovery_details = {
                "original_date": scheduled_date.isoformat(),
                "original_ist_window": f"{ist_target_start.strftime('%H:%M')} - {ist_target_end.strftime('%H:%M')} IST",
                "rescheduled_date": next_day.isoformat(),
                "rescheduled_local_time": recov_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "rescheduled_ist_time": recov_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "weekday": next_day.strftime("%A"),
                "reason": "WINDOW_EXPIRED_AVOID_AFTERNOON_DELIVERY"
            }

            audit_logger.log_event(
                event_type="SCHEDULE_WINDOW_RECOVERED",
                actor="RECOVERY_ENGINE",
                entity_type="UNIVERSITY",
                entity_id=university_name,
                details=recovery_details
            )

        return {
            "university_name": university_name,
            "country": country,
            "timezone_iana": iana_tz_name,
            "scheduled_date": scheduled_date.isoformat(),
            "window_status": window_status,
            "needs_recovery": needs_recovery,
            "ist_window_start": ist_target_start.strftime("%Y-%m-%d %H:%M:%S IST"),
            "ist_window_end": ist_target_end.strftime("%Y-%m-%d %H:%M:%S IST"),
            "current_eval_time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
            "recovery": recovery_details
        }

    def recover_batch_queue(
        self,
        queue_items: List[Dict[str, Any]],
        current_dt_ist: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Scan a queue of scheduled dispatches and recover any expired items.
        """
        active_items = []
        recovered_items = []
        upcoming_items = []

        for item in queue_items:
            uni = item.get("university_name", "")
            country = item.get("country", "")
            sched_date = item.get("target_date")
            if isinstance(sched_date, str):
                sched_date = date.fromisoformat(sched_date)
            elif not sched_date:
                sched_date = date.today()

            eval_res = self.evaluate_and_recover_window(
                university_name=uni,
                country=country,
                scheduled_date=sched_date,
                current_dt_ist=current_dt_ist
            )

            item_copy = dict(item)
            item_copy["window_evaluation"] = eval_res

            if eval_res["window_status"] == "WINDOW_ACTIVE":
                active_items.append(item_copy)
            elif eval_res["needs_recovery"]:
                item_copy["target_date"] = eval_res["recovery"]["rescheduled_date"]
                item_copy["scheduled_ist_time"] = eval_res["recovery"]["rescheduled_ist_time"]
                item_copy["status"] = "RESCHEDULED_NEXT_WEEKDAY"
                recovered_items.append(item_copy)
            else:
                upcoming_items.append(item_copy)

        return {
            "total_evaluated": len(queue_items),
            "active_ready_count": len(active_items),
            "recovered_rolled_count": len(recovered_items),
            "upcoming_count": len(upcoming_items),
            "active_items": active_items,
            "recovered_items": recovered_items,
            "upcoming_items": upcoming_items
        }


# Global singleton recovery engine
recovery_engine = MissedWindowRecoveryEngine()
