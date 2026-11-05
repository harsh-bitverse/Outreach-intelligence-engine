"""
Opportunity Intelligence Engine: Dynamic Timezone & Scheduling Engine.

Calculates recipient-local mailing windows using authentic IANA timezones and
Python's standard library `zoneinfo`, accounting for exact target-date daylight-saving
time (DST) transitions.

Reconciled with the 13 Authoritative Production Time Blocks from `queue_runner.py`
while introducing development-only extended blocks (e.g. Eastern Europe EET and India Domestic)
and strict recipient-specific eligibility calculations.
"""

from datetime import datetime, date, time, timedelta
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging

logger = logging.getLogger("timezone_engine")

IST_TZ = ZoneInfo("Asia/Kolkata")
UTC_TZ = ZoneInfo("UTC")

# 13 Authoritative Production Regional Blocks (aligned with `src/scheduler/queue_runner.py`)
PRODUCTION_TIME_BLOCKS: Dict[str, Dict[str, Any]] = {
    "BLOCK_01": {
        "name": "New Zealand",
        "start_ist": "01:00",
        "end_ist": "02:00",
        "local_target": "09:00 NZDT (UTC+13)",
        "reference_timezone": "Pacific/Auckland",
        "start_time": time(1, 0),
        "end_time": time(2, 0),
    },
    "BLOCK_02": {
        "name": "Australia Eastern Daylight",
        "start_ist": "03:00",
        "end_ist": "04:00",
        "local_target": "09:00 AEDT (UTC+11)",
        "reference_timezone": "Australia/Sydney",
        "start_time": time(3, 0),
        "end_time": time(4, 0),
    },
    "BLOCK_03": {
        "name": "Australia Central & Queensland",
        "start_ist": "04:00",
        "end_ist": "05:00",
        "local_target": "09:00 ACDT/AEST (UTC+10.5 / +10)",
        "reference_timezone": "Australia/Adelaide",
        "start_time": time(4, 0),
        "end_time": time(5, 0),
    },
    "BLOCK_04": {
        "name": "Japan & South Korea",
        "start_ist": "05:00",
        "end_ist": "06:00",
        "local_target": "09:00 JST/KST (UTC+9)",
        "reference_timezone": "Asia/Tokyo",
        "start_time": time(5, 0),
        "end_time": time(6, 0),
    },
    "BLOCK_05": {
        "name": "Greater China, Singapore, Malaysia & WA",
        "start_ist": "06:00",
        "end_ist": "07:00",
        "local_target": "09:00 CST/HKT/SGT/AWST (UTC+8)",
        "reference_timezone": "Asia/Singapore",
        "start_time": time(6, 0),
        "end_time": time(7, 0),
    },
    "BLOCK_06": {
        "name": "Middle East & Russia",
        "start_ist": "11:00",
        "end_ist": "12:00",
        "local_target": "09:00 AST/MSK (UTC+3)",
        "reference_timezone": "Asia/Riyadh",
        "start_time": time(11, 0),
        "end_time": time(12, 0),
    },
    "BLOCK_07": {
        "name": "Continental Europe (CET)",
        "start_ist": "13:00",
        "end_ist": "14:00",
        "local_target": "09:00 CET (UTC+1)",
        "reference_timezone": "Europe/Berlin",
        "start_time": time(13, 0),
        "end_time": time(14, 0),
    },
    "BLOCK_08": {
        "name": "United Kingdom & Ireland (GMT)",
        "start_ist": "14:00",
        "end_ist": "15:00",
        "local_target": "09:00 GMT (UTC+0)",
        "reference_timezone": "Europe/London",
        "start_time": time(14, 0),
        "end_time": time(15, 0),
    },
    "BLOCK_09": {
        "name": "Latin America (Argentina & Chile)",
        "start_ist": "17:00",
        "end_ist": "18:00",
        "local_target": "09:00 ART/CLST (UTC-3)",
        "reference_timezone": "America/Argentina/Buenos_Aires",
        "start_time": time(17, 0),
        "end_time": time(18, 0),
    },
    "BLOCK_10": {
        "name": "US & Canada Eastern",
        "start_ist": "18:00",  # Summer EDT; in Winter EST, 08:30 is 19:00 IST
        "end_ist": "19:00",
        "local_target": "09:00 EDT/EST (UTC-4/-5)",
        "reference_timezone": "America/New_York",
        "start_time": time(18, 0),
        "end_time": time(19, 0),
    },
    "BLOCK_11": {
        "name": "US Central",
        "start_ist": "19:00",  # Summer CDT; in Winter CST, 08:30 is 20:00 IST
        "end_ist": "20:00",
        "local_target": "09:00 CDT/CST (UTC-5/-6)",
        "reference_timezone": "America/Chicago",
        "start_time": time(19, 0),
        "end_time": time(20, 0),
    },
    "BLOCK_12": {
        "name": "Canada Mountain / US Mountain",
        "start_ist": "20:00",  # Summer MDT; in Winter MST, 08:30 is 21:00 IST
        "end_ist": "21:00",
        "local_target": "09:00 MDT/MST (UTC-6/-7)",
        "reference_timezone": "America/Denver",
        "start_time": time(20, 0),
        "end_time": time(21, 0),
    },
    "BLOCK_13": {
        "name": "US & Canada Pacific",
        "start_ist": "21:00",  # Summer PDT; in Winter PST, 08:30 is 22:00 IST
        "end_ist": "22:00",
        "local_target": "09:00 PDT/PST (UTC-7/-8)",
        "reference_timezone": "America/Los_Angeles",
        "start_time": time(21, 0),
        "end_time": time(22, 0),
    },
}

# Development-Only Extended Blocks for Regional Accuracy
DEVELOPMENT_EXTENDED_BLOCKS: Dict[str, Dict[str, Any]] = {
    "BLOCK_DEV_EAST_EU": {
        "name": "Eastern Europe (EET Standard Time)",
        "start_ist": "12:00",
        "end_ist": "13:00",
        "local_target": "08:30 - 09:30 EET (UTC+2)",
        "reference_timezone": "Europe/Athens",
        "start_time": time(12, 0),
        "end_time": time(13, 0),
    },
    "BLOCK_DOMESTIC_IN": {
        "name": "India Domestic",
        "start_ist": "08:30",
        "end_ist": "09:30",
        "local_target": "08:30 - 09:30 IST (UTC+5:30)",
        "reference_timezone": "Asia/Kolkata",
        "start_time": time(8, 30),
        "end_time": time(9, 30),
    },
}

# Canonical Regional Blocks Registry
# Unites production numeric IDs (BLOCK_01..13), legacy descriptive IDs (BLOCK_01_JP_KR..13_OCEANIA),
# and explicit canonical labels to eliminate ambiguity across operator reports.
CANONICAL_REGIONAL_BLOCKS: Dict[str, Dict[str, Any]] = {
    "BLOCK_01_NZ": {
        "canonical_id": "BLOCK_01_NZ",
        "production_numeric_id": "BLOCK_01",
        "legacy_geographic_id": "BLOCK_13_OCEANIA",
        "canonical_label": "BLOCK_01_NZ (New Zealand)",
        "name": "New Zealand",
        "target_local_window": "08:30 - 09:30 NZDT (UTC+13)",
        "reference_timezone": "Pacific/Auckland",
        "primary_timezones": ["Pacific/Auckland"],
        "approx_ist": "01:00 - 02:00 IST",
        "start_ist": "01:00",
        "end_ist": "02:00",
        "start_time": time(1, 0),
        "end_time": time(2, 0),
    },
    "BLOCK_02_AU_EAST": {
        "canonical_id": "BLOCK_02_AU_EAST",
        "production_numeric_id": "BLOCK_02",
        "legacy_geographic_id": "BLOCK_13_OCEANIA",
        "canonical_label": "BLOCK_02_AU_EAST (Australia Eastern Daylight)",
        "name": "Australia Eastern Daylight",
        "target_local_window": "08:30 - 09:30 AEDT (UTC+11)",
        "reference_timezone": "Australia/Sydney",
        "primary_timezones": ["Australia/Sydney", "Australia/Melbourne", "Australia/Hobart", "Australia/Canberra"],
        "approx_ist": "03:00 - 04:00 IST",
        "start_ist": "03:00",
        "end_ist": "04:00",
        "start_time": time(3, 0),
        "end_time": time(4, 0),
    },
    "BLOCK_03_AU_CENTRAL": {
        "canonical_id": "BLOCK_03_AU_CENTRAL",
        "production_numeric_id": "BLOCK_03",
        "legacy_geographic_id": "BLOCK_13_OCEANIA",
        "canonical_label": "BLOCK_03_AU_CENTRAL (Australia Central & Queensland)",
        "name": "Australia Central & Queensland",
        "target_local_window": "08:30 - 09:30 ACDT/AEST (UTC+10.5/+10)",
        "reference_timezone": "Australia/Adelaide",
        "primary_timezones": ["Australia/Adelaide", "Australia/Darwin", "Australia/Brisbane"],
        "approx_ist": "04:00 - 05:00 IST",
        "start_ist": "04:00",
        "end_ist": "05:00",
        "start_time": time(4, 0),
        "end_time": time(5, 0),
    },
    "BLOCK_04_JP_KR": {
        "canonical_id": "BLOCK_04_JP_KR",
        "production_numeric_id": "BLOCK_04",
        "legacy_geographic_id": "BLOCK_01_JP_KR",
        "canonical_label": "BLOCK_04_JP_KR (Japan & South Korea)",
        "name": "Japan & South Korea",
        "target_local_window": "08:30 - 09:30 JST/KST (UTC+9)",
        "reference_timezone": "Asia/Tokyo",
        "primary_timezones": ["Asia/Tokyo", "Asia/Seoul"],
        "approx_ist": "05:00 - 06:00 IST",
        "start_ist": "05:00",
        "end_ist": "06:00",
        "start_time": time(5, 0),
        "end_time": time(6, 0),
    },
    "BLOCK_05_CN_SG": {
        "canonical_id": "BLOCK_05_CN_SG",
        "production_numeric_id": "BLOCK_05",
        "legacy_geographic_id": "BLOCK_02_SG_CN_HK_TW",
        "canonical_label": "BLOCK_05_CN_SG (Greater China, Singapore, Malaysia & WA)",
        "name": "Greater China, Singapore, Malaysia & WA",
        "target_local_window": "08:30 - 09:30 CST/HKT/SGT/AWST (UTC+8)",
        "reference_timezone": "Asia/Singapore",
        "primary_timezones": ["Asia/Singapore", "Asia/Hong_Kong", "Asia/Shanghai", "Asia/Taipei", "Asia/Kuala_Lumpur", "Australia/Perth", "Asia/Macau", "Asia/Beijing"],
        "approx_ist": "06:00 - 07:00 IST",
        "start_ist": "06:00",
        "end_ist": "07:00",
        "start_time": time(6, 0),
        "end_time": time(7, 0),
    },
    "BLOCK_06_ME_RU": {
        "canonical_id": "BLOCK_06_ME_RU",
        "production_numeric_id": "BLOCK_06",
        "legacy_geographic_id": "BLOCK_05_SA_TR_RU",
        "canonical_label": "BLOCK_06_ME_RU (Middle East & Russia)",
        "name": "Middle East & Russia",
        "target_local_window": "08:30 - 09:30 AST/MSK (UTC+3)",
        "reference_timezone": "Asia/Riyadh",
        "primary_timezones": ["Asia/Riyadh", "Asia/Dubai", "Europe/Moscow", "Asia/Qatar", "Asia/Kuwait", "Asia/Baghdad", "Asia/Bahrain"],
        "approx_ist": "11:00 - 12:00 IST",
        "start_ist": "11:00",
        "end_ist": "12:00",
        "start_time": time(11, 0),
        "end_time": time(12, 0),
    },
    "BLOCK_07_CONT_EU": {
        "canonical_id": "BLOCK_07_CONT_EU",
        "production_numeric_id": "BLOCK_07",
        "legacy_geographic_id": "BLOCK_07_CONT_EU",
        "canonical_label": "BLOCK_07_CONT_EU (Continental Europe CET)",
        "name": "Continental Europe (CET)",
        "target_local_window": "08:30 - 09:30 CET/CEST (UTC+1/+2)",
        "reference_timezone": "Europe/Berlin",
        "primary_timezones": ["Europe/Berlin", "Europe/Paris", "Europe/Zurich", "Europe/Amsterdam", "Europe/Rome", "Europe/Madrid", "Europe/Stockholm", "Europe/Copenhagen", "Europe/Oslo", "Europe/Vienna", "Europe/Brussels", "Europe/Warsaw", "Europe/Prague"],
        "approx_ist": "13:00 - 14:00 IST",
        "start_ist": "13:00",
        "end_ist": "14:00",
        "start_time": time(13, 0),
        "end_time": time(14, 0),
    },
    "BLOCK_08_UK_IE": {
        "canonical_id": "BLOCK_08_UK_IE",
        "production_numeric_id": "BLOCK_08",
        "legacy_geographic_id": "BLOCK_08_UK_IE_PT",
        "canonical_label": "BLOCK_08_UK_IE (United Kingdom & Ireland GMT)",
        "name": "United Kingdom & Ireland (GMT)",
        "target_local_window": "08:30 - 09:30 GMT/BST (UTC+0/+1)",
        "reference_timezone": "Europe/London",
        "primary_timezones": ["Europe/London", "Europe/Dublin", "Europe/Lisbon"],
        "approx_ist": "14:00 - 15:00 IST",
        "start_ist": "14:00",
        "end_ist": "15:00",
        "start_time": time(14, 0),
        "end_time": time(15, 0),
    },
    "BLOCK_09_LATAM": {
        "canonical_id": "BLOCK_09_LATAM",
        "production_numeric_id": "BLOCK_09",
        "legacy_geographic_id": None,
        "canonical_label": "BLOCK_09_LATAM (Latin America)",
        "name": "Latin America (Argentina & Chile)",
        "target_local_window": "08:30 - 09:30 ART/CLST (UTC-3)",
        "reference_timezone": "America/Argentina/Buenos_Aires",
        "primary_timezones": ["America/Argentina/Buenos_Aires", "America/Santiago", "America/Sao_Paulo"],
        "approx_ist": "17:00 - 18:00 IST",
        "start_ist": "17:00",
        "end_ist": "18:00",
        "start_time": time(17, 0),
        "end_time": time(18, 0),
    },
    "BLOCK_10_US_EAST": {
        "canonical_id": "BLOCK_10_US_EAST",
        "production_numeric_id": "BLOCK_10",
        "legacy_geographic_id": "BLOCK_09_US_EAST",
        "canonical_label": "BLOCK_10_US_EAST (US & Canada Eastern)",
        "name": "US & Canada Eastern",
        "target_local_window": "08:30 - 09:30 EDT/EST (UTC-4/-5)",
        "reference_timezone": "America/New_York",
        "primary_timezones": ["America/New_York", "America/Detroit", "America/Toronto", "America/Montreal", "America/Indiana/Indianapolis"],
        "approx_ist": "18:00 - 19:00 IST",
        "start_ist": "18:00",
        "end_ist": "19:00",
        "start_time": time(18, 0),
        "end_time": time(19, 0),
    },
    "BLOCK_11_US_CENTRAL": {
        "canonical_id": "BLOCK_11_US_CENTRAL",
        "production_numeric_id": "BLOCK_11",
        "legacy_geographic_id": "BLOCK_10_US_CENTRAL",
        "canonical_label": "BLOCK_11_US_CENTRAL (US Central)",
        "name": "US Central",
        "target_local_window": "08:30 - 09:30 CDT/CST (UTC-5/-6)",
        "reference_timezone": "America/Chicago",
        "primary_timezones": ["America/Chicago", "America/Winnipeg"],
        "approx_ist": "19:00 - 20:00 IST",
        "start_ist": "19:00",
        "end_ist": "20:00",
        "start_time": time(19, 0),
        "end_time": time(20, 0),
    },
    "BLOCK_12_US_MOUNTAIN": {
        "canonical_id": "BLOCK_12_US_MOUNTAIN",
        "production_numeric_id": "BLOCK_12",
        "legacy_geographic_id": "BLOCK_11_US_MOUNTAIN",
        "canonical_label": "BLOCK_12_US_MOUNTAIN (Canada Mountain / US Mountain)",
        "name": "Canada Mountain / US Mountain",
        "target_local_window": "08:30 - 09:30 MDT/MST (UTC-6/-7)",
        "reference_timezone": "America/Denver",
        "primary_timezones": ["America/Denver", "America/Edmonton", "America/Phoenix", "America/Calgary"],
        "approx_ist": "20:00 - 21:00 IST",
        "start_ist": "20:00",
        "end_ist": "21:00",
        "start_time": time(20, 0),
        "end_time": time(21, 0),
    },
    "BLOCK_13_US_PACIFIC": {
        "canonical_id": "BLOCK_13_US_PACIFIC",
        "production_numeric_id": "BLOCK_13",
        "legacy_geographic_id": "BLOCK_12_US_PACIFIC",
        "canonical_label": "BLOCK_13_US_PACIFIC (US & Canada Pacific)",
        "name": "US & Canada Pacific",
        "target_local_window": "08:30 - 09:30 PDT/PST (UTC-7/-8)",
        "reference_timezone": "America/Los_Angeles",
        "primary_timezones": ["America/Los_Angeles", "America/Vancouver", "America/Seattle", "America/San_Francisco"],
        "approx_ist": "21:00 - 22:00 IST",
        "start_ist": "21:00",
        "end_ist": "22:00",
        "start_time": time(21, 0),
        "end_time": time(22, 0),
    },
    "BLOCK_DEV_EAST_EU": {
        "canonical_id": "BLOCK_DEV_EAST_EU",
        "production_numeric_id": "BLOCK_DEV_EAST_EU",
        "legacy_geographic_id": "BLOCK_06_EAST_EU",
        "canonical_label": "BLOCK_DEV_EAST_EU (Eastern Europe EET)",
        "name": "Eastern Europe (EET Standard Time)",
        "target_local_window": "08:30 - 09:30 EET (UTC+2)",
        "reference_timezone": "Europe/Athens",
        "primary_timezones": ["Europe/Athens", "Europe/Helsinki", "Europe/Vilnius", "Europe/Bucharest", "Europe/Sofia", "Europe/Riga", "Europe/Tallinn"],
        "approx_ist": "12:00 - 13:00 IST",
        "start_ist": "12:00",
        "end_ist": "13:00",
        "start_time": time(12, 0),
        "end_time": time(13, 0),
    },
    "BLOCK_DOMESTIC_IN": {
        "canonical_id": "BLOCK_DOMESTIC_IN",
        "production_numeric_id": "BLOCK_DOMESTIC_IN",
        "legacy_geographic_id": None,
        "canonical_label": "BLOCK_DOMESTIC_IN (India Domestic IST)",
        "name": "India Domestic",
        "target_local_window": "08:30 - 09:30 IST (UTC+5:30)",
        "reference_timezone": "Asia/Kolkata",
        "primary_timezones": ["Asia/Kolkata", "Asia/Calcutta"],
        "approx_ist": "08:30 - 09:30 IST",
        "start_ist": "08:30",
        "end_ist": "09:30",
        "start_time": time(8, 30),
        "end_time": time(9, 30),
    }
}


class CanonicalRegionalBlockRegistry:
    """
    Authoritative canonical registry that unifies:
    1. Production numeric blocks (BLOCK_01 to BLOCK_13 from queue_runner.py)
    2. Legacy descriptive block IDs (BLOCK_01_JP_KR, BLOCK_09_US_EAST from time_blocks.py)
    3. Explicit canonical identifiers and labels (e.g. BLOCK_10_US_EAST (US & Canada Eastern))
    4. Exact IANA timezone resolution and recipient-local working-day calculation.
    """

    @classmethod
    def resolve_block(cls, identifier_or_tz: str) -> Dict[str, Any]:
        """
        Resolves any block identifier or timezone into its canonical block definition.
        """
        raw = (identifier_or_tz or "").strip()
        raw_upper = raw.upper()

        # 1. Direct match on canonical_id
        if raw_upper in CANONICAL_REGIONAL_BLOCKS:
            return CANONICAL_REGIONAL_BLOCKS[raw_upper]

        # 2. Match on production numeric ID (e.g. 'BLOCK_10', 'BLOCK_04', 'BLOCK_02')
        for b in CANONICAL_REGIONAL_BLOCKS.values():
            if b["production_numeric_id"] == raw_upper:
                return b

        # 3. Match on legacy descriptive ID (e.g. 'BLOCK_09_US_EAST', 'BLOCK_01_JP_KR')
        for b in CANONICAL_REGIONAL_BLOCKS.values():
            if b.get("legacy_geographic_id") == raw_upper:
                return b

        # 4. Match on canonical label
        for b in CANONICAL_REGIONAL_BLOCKS.values():
            if raw_upper in b["canonical_label"].upper() or b["canonical_label"].upper() in raw_upper:
                return b

        # 5. Match by IANA timezone in primary_timezones
        raw_lower = raw.lower()
        for b in CANONICAL_REGIONAL_BLOCKS.values():
            if any(raw_lower == tz.lower() or raw_lower in tz.lower() for tz in b["primary_timezones"]):
                return b

        # Fallback to Continental Europe
        return CANONICAL_REGIONAL_BLOCKS["BLOCK_07_CONT_EU"]

    @classmethod
    def is_recipient_working_day(
        cls,
        iana_timezone: str,
        target_date: Optional[date] = None
    ) -> Tuple[bool, str]:
        """
        Determines whether the given target_date is a standard working day
        in the recipient's local timezone.
        For Middle East (Gulf/Saudi), working week is Sunday through Thursday.
        For Western/Asian regions, working week is Monday through Friday.
        """
        if target_date is None:
            target_date = date(2026, 11, 5)

        tz = DynamicTimezoneEngine.get_zone_info(iana_timezone)
        # Recipient midday on target date
        midday_local = datetime.combine(target_date, time(12, 0), tzinfo=tz)
        weekday = midday_local.weekday()  # Monday is 0, Sunday is 6
        day_name = midday_local.strftime("%A")

        tz_lower = iana_timezone.lower()
        is_middle_east = any(k in tz_lower for k in ["riyadh", "dubai", "qatar", "kuwait", "bahrain", "muscat", "saudi", "gulf"])

        if is_middle_east:
            # Middle East: Sunday (6), Mon (0), Tue (1), Wed (2), Thu (3)
            if weekday in (4, 5):  # Friday, Saturday
                return False, f"NON_WORKING_DAY: {day_name} is weekend in Middle East"
            else:
                return True, f"WORKING_DAY: {day_name} is working day in Middle East"
        else:
            if weekday < 5:  # Monday to Friday
                return True, f"WORKING_DAY: {day_name} is a recipient-local working day"
            else:
                return False, f"NON_WORKING_DAY: {day_name} is a recipient-local weekend"


class DynamicTimezoneEngine:
    """
    Authoritative timezone and mailing window engine.
    Computes exact daylight-saving adjusted mailing windows using Python zoneinfo.
    """

    @staticmethod
    def get_zone_info(tz_name: Optional[str]) -> ZoneInfo:
        """Safely parses IANA timezone string or returns UTC."""
        if not tz_name:
            return UTC_TZ
        try:
            return ZoneInfo(tz_name.strip())
        except (ZoneInfoNotFoundError, ValueError, Exception):
            # Known aliases and fallbacks
            cleaned = tz_name.strip()
            fallback_map = {
                "US/Eastern": "America/New_York",
                "US/Pacific": "America/Los_Angeles",
                "US/Central": "America/Chicago",
                "US/Mountain": "America/Denver",
                "GMT": "Europe/London",
                "BST": "Europe/London",
                "CET": "Europe/Berlin",
                "CEST": "Europe/Berlin",
                "EET": "Europe/Athens",
                "EEST": "Europe/Athens",
                "JST": "Asia/Tokyo",
                "KST": "Asia/Seoul",
                "SGT": "Asia/Singapore",
                "AEST": "Australia/Sydney",
                "AEDT": "Australia/Sydney",
            }
            mapped = fallback_map.get(cleaned)
            if mapped:
                try:
                    return ZoneInfo(mapped)
                except Exception:
                    pass
            logger.warning(f"Unrecognized timezone '{tz_name}', defaulting to UTC")
            return UTC_TZ

    @classmethod
    def calculate_mailing_window(
        cls,
        iana_timezone: str,
        target_date: Optional[date] = None,
        local_start_time: time = time(8, 30),
        local_end_time: time = time(9, 30)
    ) -> Dict[str, Any]:
        """
        Calculates the recipient-local mailing window and exact IST conversion
        for a specific target date, accounting for DST.

        Example (on 2026-11-05):
        - Europe/Athens (EET, UTC+2): 08:30 EET -> 12:00 IST
        - America/New_York (EST, UTC-5): 08:30 EST -> 19:00 IST
        - Europe/London (GMT, UTC+0): 08:30 GMT -> 14:00 IST
        """
        if target_date is None:
            target_date = date(2026, 11, 5)

        tz = cls.get_zone_info(iana_timezone)

        # Build naive local datetimes and attach recipient timezone
        dt_local_start = datetime.combine(target_date, local_start_time, tzinfo=tz)
        dt_local_end = datetime.combine(target_date, local_end_time, tzinfo=tz)

        # Convert to IST (Asia/Kolkata)
        dt_ist_start = dt_local_start.astimezone(IST_TZ)
        dt_ist_end = dt_local_end.astimezone(IST_TZ)

        # Check DST status
        dst_delta = dt_local_start.dst()
        is_dst = dst_delta is not None and dst_delta.total_seconds() != 0

        # Formatted strings
        ist_start_str = dt_ist_start.strftime("%H:%M")
        ist_end_str = dt_ist_end.strftime("%H:%M")
        ist_window_str = f"{ist_start_str} - {ist_end_str} IST"

        local_start_str = dt_local_start.strftime("%H:%M")
        local_end_str = dt_local_end.strftime("%H:%M")
        local_tz_abbr = dt_local_start.strftime("%Z")
        local_window_str = f"{local_start_str} - {local_end_str} {local_tz_abbr}"

        utc_offset_str = dt_local_start.strftime("%z")
        # Format UTC offset e.g. +0200 -> +02:00
        if len(utc_offset_str) == 5:
            utc_offset_str = f"{utc_offset_str[:3]}:{utc_offset_str[3:]}"

        # Determine appropriate production regional block vs development block
        assigned_block_id, block_name = cls.match_time_block(
            iana_timezone=iana_timezone,
            ist_start_time=dt_ist_start.time(),
            ist_end_time=dt_ist_end.time()
        )

        canonical_info = CanonicalRegionalBlockRegistry.resolve_block(assigned_block_id)
        is_working_day, working_day_reason = CanonicalRegionalBlockRegistry.is_recipient_working_day(iana_timezone, target_date)

        return {
            "iana_timezone": str(tz),
            "target_date": target_date.isoformat(),
            "local_mailing_window": local_window_str,
            "ist_mailing_window": ist_window_str,
            "ist_window_start": ist_start_str,
            "ist_window_end": ist_end_str,
            "utc_offset": utc_offset_str,
            "is_dst_active": is_dst,
            "timezone_abbreviation": local_tz_abbr,
            "assigned_block_id": assigned_block_id,
            "canonical_block_id": canonical_info["canonical_id"],
            "canonical_label": canonical_info["canonical_label"],
            "production_block_id": canonical_info["production_numeric_id"],
            "legacy_block_id": canonical_info.get("legacy_geographic_id"),
            "block_name": canonical_info["name"],
            "is_recipient_working_day": is_working_day,
            "working_day_reason": working_day_reason,
            "recipient_specific_eligibility": {
                "start_ist": ist_start_str,
                "end_ist": ist_end_str,
                "recipient_local_hours": "08:30 - 09:30",
                "is_working_day": is_working_day
            }
        }

    @classmethod
    def match_time_block(
        cls,
        iana_timezone: str,
        ist_start_time: time,
        ist_end_time: time
    ) -> Tuple[str, str]:
        """
        Maps the computed IST window to the closest production regional block
        or development-extended block.
        """
        tz_lower = iana_timezone.lower()

        # 1. India Domestic Check
        if "kolkata" in tz_lower or "calcutta" in tz_lower:
            return "BLOCK_DOMESTIC_IN", "India Domestic"

        # 2. Eastern Europe Check (e.g., Athens, Helsinki, Vilnius, Bucharest)
        if any(c in tz_lower for c in ["athens", "vilnius", "helsinki", "bucharest", "sofia", "riga", "tallinn"]):
            # At 12:00 - 13:00 IST in winter, this is development block BLOCK_DEV_EAST_EU
            # In production, closest is BLOCK_06 or BLOCK_07
            return "BLOCK_DEV_EAST_EU", "Eastern Europe (EET Standard Time)"

        # 3. New Zealand
        if "auckland" in tz_lower or "pacific/auckland" in tz_lower:
            return "BLOCK_01", "New Zealand"

        # 4. Australia Eastern (Sydney, Melbourne, Hobart)
        if any(c in tz_lower for c in ["sydney", "melbourne", "hobart", "canberra"]):
            return "BLOCK_02", "Australia Eastern Daylight"

        # 5. Australia Central & Queensland (Adelaide, Darwin, Brisbane)
        if any(c in tz_lower for c in ["adelaide", "darwin", "brisbane"]):
            return "BLOCK_03", "Australia Central & Queensland"

        # 6. Japan & Korea
        if any(c in tz_lower for c in ["tokyo", "seoul"]):
            return "BLOCK_04", "Japan & South Korea"

        # 7. Greater China, Singapore, Malaysia, WA (Perth)
        if any(c in tz_lower for c in ["singapore", "hong_kong", "shanghai", "taipei", "kuala_lumpur", "perth", "macau", "beijing"]):
            return "BLOCK_05", "Greater China, Singapore, Malaysia & WA"

        # 8. Middle East & Russia (Riyadh, Dubai, Moscow, Qatar)
        if any(c in tz_lower for c in ["riyadh", "dubai", "moscow", "qatar", "kuwait", "baghdad", "bahrain"]):
            return "BLOCK_06", "Middle East & Russia"

        # 9. Continental Europe (Berlin, Paris, Zurich, Rome, Amsterdam, etc.)
        if any(c in tz_lower for c in ["berlin", "paris", "zurich", "amsterdam", "rome", "madrid", "stockholm", "copenhagen", "oslo", "vienna", "brussels", "warsaw", "prague"]):
            return "BLOCK_07", "Continental Europe (CET)"

        # 10. UK & Ireland (London, Dublin, Lisbon)
        if any(c in tz_lower for c in ["london", "dublin", "lisbon"]):
            return "BLOCK_08", "United Kingdom & Ireland (GMT)"

        # 11. Latin America
        if any(c in tz_lower for c in ["buenos_aires", "santiago", "sao_paulo", "argentina"]):
            return "BLOCK_09", "Latin America (Argentina & Chile)"

        # 12. US & Canada Eastern
        if any(c in tz_lower for c in ["new_york", "toronto", "detroit", "indiana", "montreal", "eastern"]):
            return "BLOCK_10", "US & Canada Eastern"

        # 13. US Central
        if any(c in tz_lower for c in ["chicago", "central", "winnipeg"]):
            return "BLOCK_11", "US Central"

        # 14. Mountain
        if any(c in tz_lower for c in ["denver", "edmonton", "mountain", "calgary", "phoenix"]):
            return "BLOCK_12", "Canada Mountain / US Mountain"

        # 15. US & Canada Pacific
        if any(c in tz_lower for c in ["los_angeles", "vancouver", "pacific", "seattle", "san_francisco"]):
            return "BLOCK_13", "US & Canada Pacific"

        # Fallback by IST start hour
        hour = ist_start_time.hour
        for b_id, b_info in PRODUCTION_TIME_BLOCKS.items():
            b_sh = int(b_info["start_ist"].split(":")[0])
            b_eh = int(b_info["end_ist"].split(":")[0])
            if b_sh <= hour < b_eh:
                return b_id, b_info["name"]

        return "BLOCK_07", "Continental Europe (Default Fallback)"

    @classmethod
    def is_eligible_at_ist(
        cls,
        iana_timezone: str,
        current_dt_ist: Optional[datetime] = None
    ) -> Tuple[bool, str, str]:
        """
        Determines whether the recipient at `iana_timezone` is currently within
        their authorized local mailing window (08:30 - 09:30 local time).

        Returns:
            (is_eligible, reason, recipient_local_time_str)
        """
        if current_dt_ist is None:
            current_dt_ist = datetime.now(IST_TZ)
        elif current_dt_ist.tzinfo is None:
            current_dt_ist = current_dt_ist.replace(tzinfo=IST_TZ)

        tz = cls.get_zone_info(iana_timezone)
        recipient_local_dt = current_dt_ist.astimezone(tz)
        local_time = recipient_local_dt.time()
        local_time_str = recipient_local_dt.strftime("%H:%M %Z")

        # Authorized working window: 08:30 to 09:30 recipient local time
        window_start = time(8, 30)
        window_end = time(9, 30)

        if window_start <= local_time <= window_end:
            return True, f"ELIGIBLE: Recipient local time {local_time_str} is within authorized 08:30-09:30 window", local_time_str
        elif local_time < window_start:
            return False, f"TOO_EARLY: Recipient local time {local_time_str} is before 08:30 working hours", local_time_str
        else:
            return False, f"PAST_WINDOW: Recipient local time {local_time_str} has passed the 09:30 morning peak window", local_time_str

    @classmethod
    def is_recipient_in_active_window(
        cls,
        iana_timezone: str,
        now_utc: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        Calculates dynamic recipient dispatch eligibility based on:
        1. Recipient working day (accounting for regional calendars, e.g. Sun-Thu in Middle East).
        2. Recipient local morning arrival window (08:30 - 09:30 local midpoint).
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        tz = cls.get_zone_info(iana_timezone)
        recipient_local_dt = now_utc.astimezone(tz)
        local_date = recipient_local_dt.date()
        local_time = recipient_local_dt.time()
        local_time_str = recipient_local_dt.strftime("%H:%M %Z")

        # 1. Working Day Check
        is_working_day, wd_reason = CanonicalRegionalBlockRegistry.is_recipient_working_day(iana_timezone, local_date)
        if not is_working_day:
            return False, f"NON_WORKING_DAY: {wd_reason}"

        # 2. Local Time Window (08:30 to 09:30)
        window_start = time(8, 30)
        window_end = time(9, 30)
        if window_start <= local_time <= window_end:
            return True, f"ACTIVE_WINDOW: Recipient local time {local_time_str} is within 08:30-09:30 morning window"
        elif local_time < window_start:
            return False, f"OUTSIDE_HOURS: Recipient local time {local_time_str} is before 08:30"
        else:
            return False, f"OUTSIDE_HOURS: Recipient local time {local_time_str} is past 09:30"


# Alias for backward compatibility across modules
OpportunityTimezoneEngine = DynamicTimezoneEngine

