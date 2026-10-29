"""
Timezone and DST Calculation Engine.
Uses Python standard library zoneinfo to resolve university-specific time zones,
compute target local morning arrival windows (8:00 - 9:00 AM), and map to operator IST.
"""

import json
from datetime import datetime, date, time, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
from zoneinfo import ZoneInfo
from config.settings import settings


class TimezoneEngine:
    """Computes date-dependent university-local times and operator IST equivalents."""

    def __init__(self, tz_map_file: Optional[Path] = None):
        self.tz_map_file = tz_map_file or (settings.BASE_DIR / "config" / "university_timezones.json")
        self.university_timezones: Dict[str, str] = {}
        self.country_default_timezones: Dict[str, str] = {}
        self.operator_tz = ZoneInfo(settings.OPERATOR_TIMEZONE)
        self.load_mappings()

    def load_mappings(self) -> None:
        """Load timezone definitions from JSON."""
        if self.tz_map_file.exists():
            with open(self.tz_map_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.university_timezones = data.get("university_timezones", {})
                self.country_default_timezones = data.get("country_default_timezones", {})

    def resolve_timezone(self, university_name: str, country: str) -> str:
        """
        Determine the IANA timezone string for a given university and country.
        """
        if university_name in self.university_timezones:
            return self.university_timezones[university_name]
        if country in self.country_default_timezones:
            return self.country_default_timezones[country]
        return "UTC"

    def calculate_dispatch_window(
        self,
        university_name: str,
        country: str,
        target_date: Optional[date] = None,
        target_local_hour: int = 8,
        target_local_minute: int = 0
    ) -> Dict[str, Any]:
        """
        Compute the exact dispatch moment for a university-local 8:00 AM arrival,
        accounting for DST on the target date.
        """
        eval_date = target_date or date.today()
        iana_tz_name = self.resolve_timezone(university_name, country)
        uni_tz = ZoneInfo(iana_tz_name)

        # Local target time at university
        local_dt = datetime.combine(
            eval_date,
            time(hour=target_local_hour, minute=target_local_minute),
            tzinfo=uni_tz
        )

        # Convert to UTC and Operator IST
        utc_dt = local_dt.astimezone(timezone.utc)
        ist_dt = local_dt.astimezone(self.operator_tz)

        # Check if Daylight Saving Time is active on this date
        dst_offset = local_dt.dst()
        is_dst = dst_offset is not None and dst_offset.total_seconds() != 0

        # Check weekday
        weekday_name = local_dt.strftime("%A")
        is_weekday = local_dt.weekday() < 5

        return {
            "university_name": university_name,
            "country": country,
            "timezone_iana": iana_tz_name,
            "is_dst": is_dst,
            "dst_offset_seconds": dst_offset.total_seconds() if dst_offset else 0,
            "target_date": eval_date.isoformat(),
            "target_local_time": local_dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "dispatch_utc_time": utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "dispatch_ist_time": ist_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
            "ist_hour_minute": ist_dt.strftime("%I:%M %p"),
            "weekday": weekday_name,
            "is_weekday": is_weekday
        }

    def get_next_valid_weekday(self, from_date: Optional[date] = None) -> date:
        """Ensure dispatch is scheduled only on Monday-Friday."""
        curr = from_date or date.today()
        while curr.weekday() >= 5:  # 5=Saturday, 6=Sunday
            curr = curr.fromordinal(curr.toordinal() + 1)
        return curr


# Global timezone engine singleton
timezone_engine = TimezoneEngine()
