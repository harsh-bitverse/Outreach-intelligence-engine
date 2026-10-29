"""
Time Blocks and Distribution Scheduler Module.
Groups university prospects into regional time blocks targeting 8:00 - 9:00 AM local morning arrival,
computed in operator IST, sorted by country priorities and ascending QS ranking order.
"""

from datetime import date, datetime
from typing import Dict, List, Any, Optional
from src.scheduler.timezone_engine import timezone_engine


TIME_BLOCK_DEFINITIONS = [
    {
        "block_id": "BLOCK_01_JP_KR",
        "region": "Japan & South Korea",
        "countries": ["Japan", "South Korea"],
        "approx_ist": "4:30 AM - 5:00 AM IST",
        "notes": "Target 8:00 AM JST/KST (UTC+9)"
    },
    {
        "block_id": "BLOCK_02_SG_CN_HK_TW",
        "region": "Singapore, China, Hong Kong & Taiwan",
        "countries": ["Singapore", "China", "Hong Kong", "Taiwan"],
        "approx_ist": "5:30 AM IST",
        "notes": "Target 8:00 AM SGT/CST/HKT (UTC+8)"
    },
    {
        "block_id": "BLOCK_03_SEA",
        "region": "Southeast Asia",
        "countries": ["Malaysia", "Thailand", "Indonesia", "Vietnam"],
        "approx_ist": "6:30 AM IST",
        "notes": "Target 8:00 AM ICT (UTC+7)"
    },
    {
        "block_id": "BLOCK_04_GULF",
        "region": "United Arab Emirates & Gulf",
        "countries": ["United Arab Emirates", "Qatar", "Oman"],
        "approx_ist": "9:30 AM IST",
        "notes": "Target 8:00 AM GST (UTC+4)"
    },
    {
        "block_id": "BLOCK_05_SA_TR_RU",
        "region": "Saudi Arabia, Turkey & Western Russia",
        "countries": ["Saudi Arabia", "Turkey", "Russia"],
        "approx_ist": "10:30 AM IST",
        "notes": "Target 8:00 AM AST/TRT/MSK (UTC+3)"
    },
    {
        "block_id": "BLOCK_06_EAST_EU",
        "region": "Eastern Europe",
        "countries": ["Finland", "Estonia", "Greece", "Romania"],
        "approx_ist": "11:30 AM IST (winter) / 10:30 AM IST (summer)",
        "notes": "Target 8:00 AM EET/EEST (UTC+2/UTC+3 with DST)"
    },
    {
        "block_id": "BLOCK_07_CONT_EU",
        "region": "Continental Europe",
        "countries": ["Germany", "Switzerland", "France", "Netherlands", "Sweden", "Denmark", "Belgium", "Italy", "Spain", "Austria", "Norway"],
        "approx_ist": "12:30 PM IST (winter) / 11:30 AM IST (summer)",
        "notes": "Target 8:00 AM CET/CEST (UTC+1/UTC+2 with DST)"
    },
    {
        "block_id": "BLOCK_08_UK_IE_PT",
        "region": "United Kingdom, Ireland & Portugal",
        "countries": ["United Kingdom", "Ireland", "Portugal"],
        "approx_ist": "1:30 PM IST (winter) / 12:30 PM IST (summer)",
        "notes": "Target 8:00 AM GMT/BST (UTC+0/UTC+1 with DST)"
    },
    {
        "block_id": "BLOCK_09_US_EAST",
        "region": "US Eastern & Canada Eastern",
        "countries": ["United States", "Canada"],
        "tz_filter": ["America/New_York", "America/Detroit", "America/Toronto", "America/Montreal", "America/Indiana/Indianapolis"],
        "approx_ist": "6:30 PM IST (winter) / 5:30 PM IST (summer)",
        "notes": "Target 8:00 AM EST/EDT (UTC-5/UTC-4 with DST)"
    },
    {
        "block_id": "BLOCK_10_US_CENTRAL",
        "region": "US Central & Canada Central",
        "countries": ["United States", "Canada"],
        "tz_filter": ["America/Chicago", "America/Winnipeg"],
        "approx_ist": "7:30 PM IST (winter) / 6:30 PM IST (summer)",
        "notes": "Target 8:00 AM CST/CDT (UTC-6/UTC-5 with DST)"
    },
    {
        "block_id": "BLOCK_11_US_MOUNTAIN",
        "region": "US Mountain & Canada Mountain",
        "countries": ["United States", "Canada"],
        "tz_filter": ["America/Denver", "America/Edmonton", "America/Phoenix"],
        "approx_ist": "8:30 PM IST (winter) / 7:30 PM IST (summer)",
        "notes": "Target 8:00 AM MST/MDT (UTC-7/UTC-6 with DST)"
    },
    {
        "block_id": "BLOCK_12_US_PACIFIC",
        "region": "US Pacific & Canada Pacific",
        "countries": ["United States", "Canada"],
        "tz_filter": ["America/Los_Angeles", "America/Vancouver"],
        "approx_ist": "9:30 PM IST (winter) / 8:30 PM IST (summer)",
        "notes": "Target 8:00 AM PST/PDT (UTC-8/UTC-7 with DST)"
    },
    {
        "block_id": "BLOCK_13_OCEANIA",
        "region": "Australia & New Zealand",
        "countries": ["Australia", "New Zealand"],
        "approx_ist": "2:30 AM - 3:30 AM IST",
        "notes": "Target 8:00 AM AEST/NZST"
    }
]


class TimeBlockScheduler:
    """Builds and manages regional time block schedules."""

    def __init__(self, tz_engine=None):
        self.tz = tz_engine or timezone_engine

    def assign_professor_to_block(self, prof: Dict[str, Any], target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Determine which schedule block and exact IST dispatch time applies to a professor.
        """
        uni = prof.get("university_name", "")
        country = prof.get("country", "")
        iana_tz = self.tz.resolve_timezone(uni, country)
        dispatch_info = self.tz.calculate_dispatch_window(uni, country, target_date)

        # Match block
        assigned_block = None
        for b_def in TIME_BLOCK_DEFINITIONS:
            # Check timezone filter if specified
            if "tz_filter" in b_def:
                if country in b_def["countries"] and iana_tz in b_def["tz_filter"]:
                    assigned_block = b_def
                    break
            elif country in b_def["countries"]:
                assigned_block = b_def
                break

        if not assigned_block:
            # Fallback block
            assigned_block = {
                "block_id": "BLOCK_GLOBAL_OTHER",
                "region": f"Global ({country})",
                "approx_ist": dispatch_info["ist_hour_minute"] + " IST",
                "notes": f"Resolved to {iana_tz}"
            }

        return {
            "professor_id": prof.get("professor_id"),
            "full_name": prof.get("full_name"),
            "university_name": uni,
            "country": country,
            "qs_rank": prof.get("qs_rank") or 999,
            "block_id": assigned_block["block_id"],
            "region": assigned_block["region"],
            "timezone_iana": iana_tz,
            "is_dst": dispatch_info["is_dst"],
            "target_local_time": dispatch_info["target_local_time"],
            "scheduled_ist_time": dispatch_info["dispatch_ist_time"],
            "scheduled_utc_time": dispatch_info["dispatch_utc_time"],
            "weekday": dispatch_info["weekday"],
            "is_weekday": dispatch_info["is_weekday"]
        }

    def generate_schedule_blocks_summary(
        self,
        professors: List[Dict[str, Any]],
        target_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Aggregate professors into structured blocks with counts and priority ordering.
        """
        eval_date = target_date or date.today()
        # Ensure it's a weekday
        if eval_date.weekday() >= 5:
            eval_date = self.tz.get_next_valid_weekday(eval_date)

        blocks_map: Dict[str, Dict[str, Any]] = {}
        for b_def in TIME_BLOCK_DEFINITIONS:
            blocks_map[b_def["block_id"]] = {
                "block_id": b_def["block_id"],
                "region": b_def["region"],
                "ist_time": b_def["approx_ist"],
                "local_time": "8:00 AM - 9:00 AM Local",
                "countries": ", ".join(b_def["countries"]),
                "university_count": 0,
                "faculty_count": 0,
                "universities_set": set(),
                "professors": [],
                "notes": b_def["notes"]
            }

        # Assign professors
        for prof in professors:
            assigned = self.assign_professor_to_block(prof, eval_date)
            b_id = assigned["block_id"]
            if b_id in blocks_map:
                blocks_map[b_id]["professors"].append(assigned)
                blocks_map[b_id]["universities_set"].add(assigned["university_name"])

        # Compute counts and sort professors by QS Rank
        summary_list = []
        for b_def in TIME_BLOCK_DEFINITIONS:
            b_id = b_def["block_id"]
            block_data = blocks_map[b_id]
            block_data["university_count"] = len(block_data["universities_set"])
            block_data["faculty_count"] = len(block_data["professors"])
            # Ascending QS rank order
            block_data["professors"].sort(key=lambda p: (p["qs_rank"], p["full_name"]))
            # Clean set for JSON serialization
            del block_data["universities_set"]
            summary_list.append(block_data)

        return summary_list


# Global time block scheduler instance
time_block_scheduler = TimeBlockScheduler()
