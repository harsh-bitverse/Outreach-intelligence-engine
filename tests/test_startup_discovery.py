"""
Automated Test Suite for Global Frontier-Tech Startup Discovery (Session 02).
Tests:
1. Startup catalog integrity (>= 100 distinct startups, valid schemas).
2. StartupAdapter ingestion, grounding verification, and normalization.
3. Qualification and multi-factor ranking with PriorityTier.
4. Resumable discovery checkpoints.
5. Unified regional time-block scheduling (13 IST blocks + India Domestic).
6. Domain-adaptive AI personalization (founder engineering pitch vs portal statement).
7. Shared global sending budget & cooldown management.
8. Multi-sheet Excel sync preserving legacy sheets.
9. Absolute production database isolation.
"""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, Any

import openpyxl
import pytest

from config.settings import settings
from src.opportunities.models import (
    Organization,
    Opportunity,
    OpportunityType,
    CompensationType,
    ContactChannel,
    PriorityTier,
    DiscoveryStatus
)
from src.opportunities.schema import apply_opportunity_migrations
from src.opportunities.repository import OpportunityRepository
from src.opportunities.registry import SourceRegistry, canonicalize_url
from src.opportunities.ranking import OpportunityRanker
from src.opportunities.adapters.startup import StartupAdapter
from src.opportunities.data.startup_catalog import STARTUP_CATALOG
from src.opportunities.scheduling import OpportunityScheduler
from src.opportunities.personalization import DomainAdaptivePersonalizer
from src.opportunities.budget_and_tracking import GlobalBudgetManager, UnifiedDeliveryBridge
from src.opportunities.workbook_sync import OpportunityWorkbookSync


@pytest.fixture
def isolated_startup_env():
    """Create isolated SQLite database and Excel workbook for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        tmp_db = tmp_dir / "test_opportunity_engine.db"
        tmp_wb = tmp_dir / "test_opportunity_workbook.xlsx"
        tmp_lock = tmp_dir / "test_discovery.lock"

        # Copy existing master workbook and DB
        shutil.copy2(settings.MASTER_WORKBOOK_PATH, tmp_wb)
        shutil.copy2(settings.SQLITE_DB_PATH, tmp_db)

        # Apply opportunity migrations to isolated DB
        conn = sqlite3.connect(tmp_db)
        apply_opportunity_migrations(conn)
        repo = OpportunityRepository(conn)
        sync = OpportunityWorkbookSync(workbook_path=tmp_wb)

        yield {
            "tmp_dir": tmp_dir,
            "db_path": tmp_db,
            "workbook_path": tmp_wb,
            "lock_path": tmp_lock,
            "conn": conn,
            "repo": repo,
            "sync": sync,
        }
        conn.close()


def test_startup_catalog_scale_and_fields():
    """Verify catalog has at least 100 distinct real startups with complete fields."""
    assert len(STARTUP_CATALOG) >= 100, f"Expected >= 100 startups, found {len(STARTUP_CATALOG)}"

    distinct_names = {s["company_name"].strip().lower() for s in STARTUP_CATALOG}
    assert len(distinct_names) == len(STARTUP_CATALOG), "Duplicate company names found in catalog!"

    # Verify essential fields in every entry
    required_keys = ["company_name", "website", "country", "city", "timezone", "domains", "title", "evidence_url"]
    for s in STARTUP_CATALOG:
        for k in required_keys:
            assert k in s and s[k], f"Startup {s.get('company_name')} missing required key '{k}'"
        assert s["website"].startswith("http://") or s["website"].startswith("https://")


def test_startup_adapter_ingestion_and_grounding(isolated_startup_env):
    """Verify StartupAdapter ingests and grounds real startups."""
    adapter = StartupAdapter()
    raw_items = adapter.fetch_raw_opportunities(limit=10)
    assert len(raw_items) == 10

    for item in raw_items:
        org, opp = adapter.normalize_raw_item(item)
        assert org.organization_id.startswith("org_")
        assert opp.opportunity_id.startswith("opp_")
        assert opp.opportunity_type == OpportunityType.STARTUP.value
        assert adapter.verify_grounding(opp) is True


def test_startup_ranking_and_priority_tiers():
    """Verify OpportunityRanker scores startups and assigns priority tiers."""
    adapter = StartupAdapter()
    raw_item = STARTUP_CATALOG[0]  # Sarvam AI
    org, opp = adapter.normalize_raw_item(raw_item)

    score, breakdown, status, matched_project = OpportunityRanker.evaluate_opportunity(opp)
    assert score >= 75.0
    assert opp.priority_tier in (PriorityTier.HIGH_PRIORITY.value, PriorityTier.PROMISING.value)
    assert matched_project in ("LLM Inference Intelligence Engine", "Neural Process Twin")
    assert breakdown.compensation > 0.0
    assert breakdown.technical_fit > 0.0


def test_resumable_checkpoints(isolated_startup_env):
    """Verify discovery runs save checkpoints and subsequent runs resume from cursor."""
    repo = isolated_startup_env["repo"]
    adapter = StartupAdapter()

    # Batch 1: first 5 items
    run1 = adapter.run_discovery_cycle(repo, limit=5)
    assert run1.opportunities_found == 5
    cp1 = repo.get_checkpoint(adapter.adapter_name)
    assert cp1 is not None
    assert int(cp1.cursor_or_token) == 5

    # Batch 2: next 5 items
    run2 = adapter.run_discovery_cycle(repo, limit=5)
    assert run2.opportunities_found == 5
    cp2 = repo.get_checkpoint(adapter.adapter_name)
    assert int(cp2.cursor_or_token) == 10


def test_unified_regional_time_block_scheduling():
    """Verify time-block resolution assigns contacts to correct regional IST blocks."""
    # India domestic
    b_in = OpportunityScheduler.resolve_time_block("Asia/Kolkata", "India")
    assert b_in["block_id"] == "BLOCK_DOMESTIC_IN"
    assert "08:00 - 09:30 IST" in b_in["ist_window"]

    # US Pacific
    b_sf = OpportunityScheduler.resolve_time_block("America/Los_Angeles", "United States")
    assert b_sf["block_id"] == "BLOCK_12_US_PACIFIC"

    # US Eastern
    b_nyc = OpportunityScheduler.resolve_time_block("America/New_York", "United States")
    assert b_nyc["block_id"] == "BLOCK_09_US_EAST"

    # UK
    b_uk = OpportunityScheduler.resolve_time_block("Europe/London", "United Kingdom")
    assert b_uk["block_id"] == "BLOCK_08_UK_IE_PT"

    # Continental Europe
    b_de = OpportunityScheduler.resolve_time_block("Europe/Berlin", "Germany")
    assert b_de["block_id"] == "BLOCK_07_CONT_EU"

    # Japan
    b_jp = OpportunityScheduler.resolve_time_block("Asia/Tokyo", "Japan")
    assert b_jp["block_id"] == "BLOCK_01_JP_KR"


def test_domain_adaptive_personalization():
    """Verify domain-adaptive personalizer creates high-agency startup pitches."""
    adapter = StartupAdapter()
    raw_item = STARTUP_CATALOG[0]
    org, opp = adapter.normalize_raw_item(raw_item)
    OpportunityRanker.evaluate_opportunity(opp)

    pitch = DomainAdaptivePersonalizer.generate_startup_pitch(opp, org)
    assert pitch["tone"] == "CONCISE_FOUNDER_ENGINEERING_PITCH"
    assert "Harsh Vardhan" in pitch["pitch_body"]
    assert "IIT Delhi" in pitch["pitch_body"]
    assert len(pitch["pitch_subject"]) > 10

    # Portal statement test
    statement = DomainAdaptivePersonalizer.generate_portal_cover_statement(opp, org)
    assert statement["format"] == "OFFICIAL_CAREERS_PORTAL_APPLICATION"
    assert "IIT Delhi" in statement["cover_statement"]


def test_shared_global_budget_and_cooldown():
    """Verify global budget manager enforces 200/day limit and cooldowns."""
    manager = GlobalBudgetManager(daily_limit=200)

    # Professor dispatch should be allowed within limit
    can_disp, msg = manager.can_dispatch("ACADEMIC_PROFESSOR", requested_count=10)
    assert can_disp is True

    # Record 200 dispatches
    manager.record_dispatch("ACADEMIC_PROFESSOR", count=200)
    assert manager.remaining_budget_today() == 0

    # Next dispatch must be rejected
    can_disp2, msg2 = manager.can_dispatch("ACADEMIC_PROFESSOR", requested_count=1)
    assert can_disp2 is False
    assert "DAILY_LIMIT_EXCEEDED" in msg2

    # New category sending requires separate authorization
    can_disp_startup, msg_st = manager.can_dispatch("STARTUP", requested_count=1)
    assert can_disp_startup is False
    assert "CATEGORY_SENDING_NOT_AUTHORIZED" in msg_st


def test_multi_sheet_excel_sync_preserves_existing_sheets(isolated_startup_env):
    """Verify workbook sync populates Startups and Unified Outreach Schedule sheets without modifying legacy sheets."""
    wb_p = isolated_startup_env["workbook_path"]
    sync = isolated_startup_env["sync"]
    repo = isolated_startup_env["repo"]

    # Initial sheet row check
    wb_pre = openpyxl.load_workbook(str(wb_p), data_only=True)
    tb_rows_pre = wb_pre["Time Blocks & Distribution"].max_row
    pd_rows_pre = wb_pre["Professor Database"].max_row
    wb_pre.close()

    # Discover and qualify 15 startups
    adapter = StartupAdapter()
    run = adapter.run_discovery_cycle(repo, limit=15)
    assert run.opportunities_found == 15

    opps = repo.list_opportunities(category=OpportunityType.STARTUP.value, limit=15)
    orgs = {opp.organization_id: repo.get_organization(opp.organization_id) for opp in opps}

    sync.initialize_opportunity_sheets()
    sync_counts = sync.sync_opportunities(opps, orgs)
    assert sync_counts["Startups"] == 15

    # Schedule sync
    schedule_records = OpportunityScheduler.generate_unified_schedule(opps, orgs)
    sched_count = sync.sync_unified_schedule(schedule_records)
    assert sched_count == 15

    # Verify legacy sheets were 100% preserved
    wb_post = openpyxl.load_workbook(str(wb_p), data_only=True)
    assert wb_post["Time Blocks & Distribution"].max_row == tb_rows_pre
    assert wb_post["Professor Database"].max_row == pd_rows_pre
    assert wb_post["Startups"].max_row >= 16  # header + 15 rows
    assert wb_post["Unified Outreach Schedule"].max_row >= 16
    wb_post.close()


def test_production_database_isolation():
    """Verify live production database contains zero opportunity records."""
    prod_db = settings.SQLITE_DB_PATH
    assert prod_db.exists()
    conn = sqlite3.connect(prod_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities';")
    row = cur.fetchone()
    conn.close()
    assert row is None, "Production database must NEVER be migrated in development sessions!"
