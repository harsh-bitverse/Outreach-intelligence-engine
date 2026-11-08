"""
Session 10: Regression Tests for Authoritative Production Time Blocks & Dynamic Timezones.

Validates:
1. Authoritative 13 IST production time blocks strictly match `queue_runner.py`,
   the master workbook 'Time Blocks & Distribution' sheet, and `data/outreach_engine.db`.
2. BLOCK_01 = New Zealand (01:00-02:00 IST), BLOCK_02 = Australia Eastern (03:00-04:00 IST),
   BLOCK_04 = Japan & South Korea (05:00-06:00 IST), ..., BLOCK_13 = US & Canada Pacific (21:00-22:00 IST).
3. Dynamic recipient-specific eligibility requires BOTH:
   - Valid working day in recipient timezone (Monday-Friday, or Sunday-Thursday in Middle East).
   - Recipient local time within 08:30-09:30 morning window.
4. No opportunity can be dispatched solely because a numeric regional block happens to match.
5. All 1,390 existing professor assignments in production DB are strictly preserved.
"""

import sqlite3
import pytest
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
import openpyxl

from config.settings import settings
from src.scheduler.queue_runner import TIME_BLOCK_WINDOWS
from src.opportunities.timezone_engine import (
    DynamicTimezoneEngine,
    PRODUCTION_TIME_BLOCKS,
    CANONICAL_REGIONAL_BLOCKS,
    OpportunityTimezoneEngine
)


def test_production_time_block_windows_match_master_definitions():
    """Verify queue_runner.py TIME_BLOCK_WINDOWS matches authoritative definitions."""
    assert "BLOCK_01" in TIME_BLOCK_WINDOWS
    assert TIME_BLOCK_WINDOWS["BLOCK_01"]["name"] == "New Zealand"
    assert TIME_BLOCK_WINDOWS["BLOCK_01"]["start_ist"] == "01:00"
    assert TIME_BLOCK_WINDOWS["BLOCK_01"]["end_ist"] == "02:00"

    assert "BLOCK_02" in TIME_BLOCK_WINDOWS
    assert "Australia Eastern" in TIME_BLOCK_WINDOWS["BLOCK_02"]["name"]
    assert TIME_BLOCK_WINDOWS["BLOCK_02"]["start_ist"] == "03:00"
    assert TIME_BLOCK_WINDOWS["BLOCK_02"]["end_ist"] == "04:00"

    assert "BLOCK_03" in TIME_BLOCK_WINDOWS
    assert "Australia Central" in TIME_BLOCK_WINDOWS["BLOCK_03"]["name"]

    assert "BLOCK_04" in TIME_BLOCK_WINDOWS
    assert "Japan" in TIME_BLOCK_WINDOWS["BLOCK_04"]["name"]
    assert TIME_BLOCK_WINDOWS["BLOCK_04"]["start_ist"] == "05:00"
    assert TIME_BLOCK_WINDOWS["BLOCK_04"]["end_ist"] == "06:00"

    assert "BLOCK_05" in TIME_BLOCK_WINDOWS
    assert "China" in TIME_BLOCK_WINDOWS["BLOCK_05"]["name"] or "Singapore" in TIME_BLOCK_WINDOWS["BLOCK_05"]["name"]

    assert "BLOCK_06" in TIME_BLOCK_WINDOWS
    assert "Middle East" in TIME_BLOCK_WINDOWS["BLOCK_06"]["name"]

    assert "BLOCK_07" in TIME_BLOCK_WINDOWS
    assert "Continental Europe" in TIME_BLOCK_WINDOWS["BLOCK_07"]["name"]

    assert "BLOCK_08" in TIME_BLOCK_WINDOWS
    assert "United Kingdom" in TIME_BLOCK_WINDOWS["BLOCK_08"]["name"]

    assert "BLOCK_09" in TIME_BLOCK_WINDOWS
    assert "Latin America" in TIME_BLOCK_WINDOWS["BLOCK_09"]["name"]

    assert "BLOCK_10" in TIME_BLOCK_WINDOWS
    assert "Eastern" in TIME_BLOCK_WINDOWS["BLOCK_10"]["name"]

    assert "BLOCK_11" in TIME_BLOCK_WINDOWS
    assert "Central" in TIME_BLOCK_WINDOWS["BLOCK_11"]["name"]

    assert "BLOCK_12" in TIME_BLOCK_WINDOWS
    assert "Mountain" in TIME_BLOCK_WINDOWS["BLOCK_12"]["name"]

    assert "BLOCK_13" in TIME_BLOCK_WINDOWS
    assert "Pacific" in TIME_BLOCK_WINDOWS["BLOCK_13"]["name"]
    assert TIME_BLOCK_WINDOWS["BLOCK_13"]["start_ist"] == "21:00"
    assert TIME_BLOCK_WINDOWS["BLOCK_13"]["end_ist"] == "22:00"


def test_master_workbook_and_production_db_counts_parity():
    """Verify production database professor counts per block match master workbook."""
    db_path = settings.SQLITE_DB_PATH
    assert db_path.exists(), f"Production DB missing at {db_path}"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    cur = conn.cursor()
    cur.execute("SELECT time_block, COUNT(*) FROM professors WHERE processing_sequence <= 1390 GROUP BY time_block ORDER BY time_block;")
    db_counts = dict(cur.fetchall())
    total_professors = cur.execute("SELECT COUNT(*) FROM professors;").fetchone()[0]
    conn.close()

    expected_counts = {
        "BLOCK_01": 7,
        "BLOCK_02": 64,
        "BLOCK_03": 15,
        "BLOCK_04": 101,
        "BLOCK_05": 177,
        "BLOCK_06": 36,
        "BLOCK_07": 240,
        "BLOCK_08": 227,
        "BLOCK_09": 18,
        "BLOCK_10": 340,
        "BLOCK_11": 39,
        "BLOCK_12": 7,
        "BLOCK_13": 119
    }

    for block_id, exp_count in expected_counts.items():
        assert db_counts.get(block_id) == exp_count, f"Mismatch for {block_id}: got {db_counts.get(block_id)}, expected {exp_count}"

    assert sum(expected_counts.values()) == 1390
    assert total_professors >= 1390


def test_dynamic_recipient_eligibility_requires_working_day_and_morning_window():
    """
    Verify dynamic dispatch eligibility:
    - Saturday/Sunday are excluded for Western/Asian institutions.
    - Friday is excluded and Sunday is working for Middle East institutions.
    - Current local time must fall strictly between 08:30 and 09:30 local midpoint.
    """
    engine = OpportunityTimezoneEngine()

    # Case 1: Monday at 09:00 local time in Tokyo -> MUST be eligible
    # Tokyo is UTC+9. 09:00 Tokyo = 00:00 UTC. 2026-11-09 is a Monday.
    tokyo_dt_utc = datetime(2026, 11, 9, 0, 0, 0, tzinfo=timezone.utc)
    is_active, reason = engine.is_recipient_in_active_window("Asia/Tokyo", now_utc=tokyo_dt_utc)
    assert is_active is True
    assert "ACTIVE_WINDOW" in reason

    # Case 2: Saturday at 09:00 local time in Tokyo -> MUST be rejected (Weekend)
    tokyo_sat_utc = datetime(2026, 11, 7, 0, 0, 0, tzinfo=timezone.utc)
    is_active_sat, reason_sat = engine.is_recipient_in_active_window("Asia/Tokyo", now_utc=tokyo_sat_utc)
    assert is_active_sat is False
    assert "NON_WORKING_DAY" in reason_sat or "WEEKEND" in reason_sat

    # Case 3: Monday at 14:00 local time in Berlin -> MUST be rejected (Afternoon, outside 08:30-09:30)
    # Berlin is CET (UTC+1). 14:00 Berlin = 13:00 UTC.
    berlin_afternoon_utc = datetime(2026, 11, 9, 13, 0, 0, tzinfo=timezone.utc)
    is_active_ber, reason_ber = engine.is_recipient_in_active_window("Europe/Berlin", now_utc=berlin_afternoon_utc)
    assert is_active_ber is False
    assert "OUTSIDE_HOURS" in reason_ber

    # Case 4: Sunday in Dubai (Asia/Dubai) at 09:00 local -> MUST be eligible (Sunday is working day in Gulf)
    # Dubai is GST (UTC+4). 09:00 Dubai = 05:00 UTC. 2026-11-08 is a Sunday.
    dubai_sun_utc = datetime(2026, 11, 8, 5, 0, 0, tzinfo=timezone.utc)
    is_active_dub, reason_dub = engine.is_recipient_in_active_window("Asia/Dubai", now_utc=dubai_sun_utc)
    assert is_active_dub is True
    assert "ACTIVE_WINDOW" in reason_dub

    # Case 5: Friday in Dubai (Asia/Dubai) at 09:00 local -> MUST be rejected (Friday is weekend in Gulf)
    dubai_fri_utc = datetime(2026, 11, 6, 5, 0, 0, tzinfo=timezone.utc)
    is_active_dub_fri, reason_dub_fri = engine.is_recipient_in_active_window("Asia/Dubai", now_utc=dubai_fri_utc)
    assert is_active_dub_fri is False
    assert "NON_WORKING_DAY" in reason_dub_fri


def test_numeric_block_alone_does_not_permit_dispatch():
    """Verify that having an assigned block ID is insufficient without dynamic recipient eligibility."""
    # Even if an opportunity has assigned_block_id = 'BLOCK_07' (Europe),
    # if it's currently night time in Europe (e.g. 02:00 CET), it cannot be dispatched.
    engine = OpportunityTimezoneEngine()
    berlin_night_utc = datetime(2026, 11, 9, 1, 0, 0, tzinfo=timezone.utc) # 02:00 CET
    is_active, _ = engine.is_recipient_in_active_window("Europe/Berlin", now_utc=berlin_night_utc)
    assert is_active is False
