"""
Unit Tests for Scheduler, IANA Zoneinfo Resolution, Daylight Saving Time (DST), and Regional Blocks.
"""

from datetime import date
import pytest
from src.scheduler.timezone_engine import timezone_engine
from src.scheduler.time_blocks import time_block_scheduler


def test_timezone_resolution():
    """Verify IANA timezone resolution for key universities."""
    assert timezone_engine.resolve_timezone("Massachusetts Institute of Technology (MIT)", "United States") == "America/New_York"
    assert timezone_engine.resolve_timezone("Stanford University", "United States") == "America/Los_Angeles"
    assert timezone_engine.resolve_timezone("University of Cambridge", "United Kingdom") == "Europe/London"
    assert timezone_engine.resolve_timezone("The University of Tokyo", "Japan") == "Asia/Tokyo"
    assert timezone_engine.resolve_timezone("National University of Singapore (NUS)", "Singapore") == "Asia/Singapore"
    assert timezone_engine.resolve_timezone("ETH Zurich", "Switzerland") == "Europe/Zurich"


def test_dst_transition_us_eastern():
    """Verify US Eastern DST shift between winter (EST) and summer (EDT)."""
    uni = "Massachusetts Institute of Technology (MIT)"
    country = "United States"

    # Winter date (January 15, 2027) - EST (UTC-5)
    winter_date = date(2027, 1, 15)
    winter_res = timezone_engine.calculate_dispatch_window(uni, country, target_date=winter_date)
    assert winter_res["is_dst"] is False
    # 8:00 AM EST = 13:00 UTC = 18:30 (6:30 PM) IST
    assert winter_res["ist_hour_minute"] == "06:30 PM"

    # Summer date (June 15, 2027) - EDT (UTC-4)
    summer_date = date(2027, 6, 15)
    summer_res = timezone_engine.calculate_dispatch_window(uni, country, target_date=summer_date)
    assert summer_res["is_dst"] is True
    # 8:00 AM EDT = 12:00 UTC = 17:30 (5:30 PM) IST
    assert summer_res["ist_hour_minute"] == "05:30 PM"


def test_dst_transition_uk():
    """Verify UK DST shift between winter (GMT) and summer (BST)."""
    uni = "University of Oxford"
    country = "United Kingdom"

    # Winter date (January 15, 2027) - GMT (UTC+0)
    winter_date = date(2027, 1, 15)
    winter_res = timezone_engine.calculate_dispatch_window(uni, country, target_date=winter_date)
    assert winter_res["is_dst"] is False
    # 8:00 AM GMT = 08:00 UTC = 13:30 (1:30 PM) IST
    assert winter_res["ist_hour_minute"] == "01:30 PM"

    # Summer date (June 15, 2027) - BST (UTC+1)
    summer_date = date(2027, 6, 15)
    summer_res = timezone_engine.calculate_dispatch_window(uni, country, target_date=summer_date)
    assert summer_res["is_dst"] is True
    # 8:00 AM BST = 07:00 UTC = 12:30 (12:30 PM) IST
    assert summer_res["ist_hour_minute"] == "12:30 PM"


def test_time_block_summary_and_qs_sorting():
    """Verify professors are assigned to proper regional blocks and sorted by ascending QS rank."""
    profs = [
        {"professor_id": "p1", "full_name": "Prof LowRank MIT", "university_name": "Massachusetts Institute of Technology (MIT)", "country": "United States", "qs_rank": 1},
        {"professor_id": "p2", "full_name": "Prof HighRank Harvard", "university_name": "Harvard University", "country": "United States", "qs_rank": 4},
        {"professor_id": "p3", "full_name": "Prof Cambridge", "university_name": "University of Cambridge", "country": "United Kingdom", "qs_rank": 2},
        {"professor_id": "p4", "full_name": "Prof Tokyo", "university_name": "The University of Tokyo", "country": "Japan", "qs_rank": 28}
    ]

    target_date = date(2027, 2, 1)  # Monday
    blocks = time_block_scheduler.generate_schedule_blocks_summary(profs, target_date)

    # Find Japan block
    jp_block = next(b for b in blocks if b["block_id"] == "BLOCK_01_JP_KR")
    assert jp_block["faculty_count"] == 1
    assert jp_block["professors"][0]["full_name"] == "Prof Tokyo"

    # Find UK block
    uk_block = next(b for b in blocks if b["block_id"] == "BLOCK_08_UK_IE_PT")
    assert uk_block["faculty_count"] == 1
    assert uk_block["professors"][0]["full_name"] == "Prof Cambridge"

    # Find US East block
    us_east = next(b for b in blocks if b["block_id"] == "BLOCK_09_US_EAST")
    assert us_east["faculty_count"] == 2
    # Verify ascending QS rank ordering (Rank 1 before Rank 4)
    assert us_east["professors"][0]["qs_rank"] == 1
    assert us_east["professors"][1]["qs_rank"] == 4
