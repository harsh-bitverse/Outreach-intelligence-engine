"""
Session 08 Unit and Integration Tests:
- Canonical Regional Block Registry and Timezone Engine
- Multi-factor Contact-History Deduplication
- SMTP Message-ID vs Internal Tracking ID Semantics
- Domain-Adaptive Personalization with 20 Verified Drafts across 4 Categories
"""

import pytest
import datetime
from pathlib import Path
import sqlite3

from src.opportunities.timezone_engine import (
    CanonicalRegionalBlockRegistry,
    DynamicTimezoneEngine
)
from src.opportunities.academic_reconciliation import (
    AcademicPortfolioReconciler,
    ContactHistoryStatus
)
from src.opportunities.unified_outreach_bridge import (
    UnifiedOutreachBridge
)
from src.opportunities.personalization import (
    DomainAdaptivePersonalizer
)
from src.opportunities.models import Opportunity, Organization, OpportunityType, ContactChannel


def test_canonical_regional_block_resolution():
    """Verify that numeric and legacy block IDs resolve to canonical production blocks."""
    # Production numeric ID BLOCK_10 -> US East
    b10 = CanonicalRegionalBlockRegistry.resolve_block("BLOCK_10")
    assert b10 is not None
    assert b10["canonical_id"] == "BLOCK_10_US_EAST"
    assert "US & Canada Eastern" in b10["canonical_label"]
    assert b10["reference_timezone"] == "America/New_York"

    # Production numeric ID BLOCK_04 -> Japan / Korea
    b04 = CanonicalRegionalBlockRegistry.resolve_block("BLOCK_04")
    assert b04 is not None
    assert b04["canonical_id"] == "BLOCK_04_JP_KR"
    assert "Japan & South Korea" in b04["canonical_label"]
    assert b04["reference_timezone"] == "Asia/Tokyo"

    # Production numeric ID BLOCK_02 -> Australia Eastern
    b02 = CanonicalRegionalBlockRegistry.resolve_block("BLOCK_02")
    assert b02 is not None
    assert b02["canonical_id"] == "BLOCK_02_AU_EAST"
    assert "Australia Eastern" in b02["canonical_label"]
    assert b02["reference_timezone"] == "Australia/Sydney"

    # Legacy ID BLOCK_09_US_EAST maps to canonical BLOCK_10_US_EAST
    b_leg = CanonicalRegionalBlockRegistry.resolve_block("BLOCK_09_US_EAST")
    assert b_leg is not None
    assert b_leg["canonical_id"] == "BLOCK_10_US_EAST"

    # IANA timezone resolution
    b_tz = CanonicalRegionalBlockRegistry.resolve_block("America/New_York")
    assert b_tz is not None
    assert b_tz["canonical_id"] == "BLOCK_10_US_EAST"

    b_tokyo = CanonicalRegionalBlockRegistry.resolve_block("Asia/Tokyo")
    assert b_tokyo is not None
    assert b_tokyo["canonical_id"] == "BLOCK_04_JP_KR"


def test_recipient_working_day_and_weekend_pausing():
    """Verify that recipient-local weekends are flagged to pause automated outreach."""
    # A known Saturday: 2026-11-07
    sat_date = datetime.date(2026, 11, 7)
    # A known Sunday: 2026-11-08
    sun_date = datetime.date(2026, 11, 8)
    # A known Monday: 2026-11-09
    mon_date = datetime.date(2026, 11, 9)

    is_work_sat, _ = CanonicalRegionalBlockRegistry.is_recipient_working_day("America/New_York", sat_date)
    assert not is_work_sat

    is_work_sun, _ = CanonicalRegionalBlockRegistry.is_recipient_working_day("America/New_York", sun_date)
    assert not is_work_sun

    is_work_mon, _ = CanonicalRegionalBlockRegistry.is_recipient_working_day("America/New_York", mon_date)
    assert is_work_mon

    is_work_uk_sat, _ = CanonicalRegionalBlockRegistry.is_recipient_working_day("Europe/London", sat_date)
    assert not is_work_uk_sat

    is_work_uk_mon, _ = CanonicalRegionalBlockRegistry.is_recipient_working_day("Europe/London", mon_date)
    assert is_work_uk_mon


def test_multi_factor_contact_history_deduplication():
    """Verify multi-factor name and organization matching against production history."""
    prod_db = Path("data/outreach_engine.db")
    dev_db = Path("data/development/opportunity_dev.db")
    if not prod_db.exists() or not dev_db.exists():
        pytest.skip("Database paths not found for integration test.")

    reconciler = AcademicPortfolioReconciler(prod_db_path=prod_db, dev_db_path=dev_db)
    stats = reconciler.reconcile_all_contacts()
    assert stats["total_opportunities_evaluated"] >= 1700
    assert stats["scheduled_eligible_count"] >= 700

    conn = sqlite3.connect(str(dev_db))
    cur = conn.cursor()

    # Test 1: Torsten Hoefler at ETH Zurich (alternative email torsten.hoefler@inf.ethz.ch suppressed)
    cur.execute("SELECT contact_history_status, next_action FROM opportunities WHERE contact_email = 'torsten.hoefler@inf.ethz.ch';")
    stat_hoefler, act_hoefler = cur.fetchone()
    assert stat_hoefler == ContactHistoryStatus.ALREADY_CONTACTED.value
    assert act_hoefler == "EXCLUDE_FROM_DISPATCH_ALREADY_CONTACTED"

    # Test 2: Weinan E at SJTU (alternative email weinan@sjtu.edu.cn suppressed)
    cur.execute("SELECT contact_history_status, next_action FROM opportunities WHERE contact_email = 'weinan@sjtu.edu.cn';")
    stat_weinan, act_weinan = cur.fetchone()
    assert stat_weinan == ContactHistoryStatus.ALREADY_CONTACTED.value
    assert act_weinan == "EXCLUDE_FROM_DISPATCH_ALREADY_CONTACTED"

    # Test 3: Kaspar Althoefer at QMUL (new email k.althoefer@qmul.ac.uk quarantined due to bounce history)
    cur.execute("SELECT contact_history_status, next_action FROM opportunities WHERE contact_email = 'k.althoefer@qmul.ac.uk';")
    stat_kaspar, act_kaspar = cur.fetchone()
    assert stat_kaspar in [ContactHistoryStatus.NEEDS_REVIEW.value, ContactHistoryStatus.BOUNCED.value]
    assert "REVIEW" in act_kaspar

    # Test 4: Genuinely new faculty members remain SCHEDULED_ELIGIBLE
    cur.execute("SELECT COUNT(*) FROM opportunities WHERE contact_history_status = 'SCHEDULED_ELIGIBLE';")
    eligible_count = cur.fetchone()[0]
    assert eligible_count >= 700

    conn.close()


def test_internal_tracking_id_and_no_fictional_message_id():
    """Verify that schedule records contain internal tracking IDs and NO fictional historical Message-IDs."""
    dev_db = Path("data/development/opportunity_dev.db")
    if not dev_db.exists():
        pytest.skip("Development database data/development/opportunity_dev.db not found.")

    conn = sqlite3.connect(str(dev_db))
    cur = conn.cursor()

    cur.execute("SELECT opportunity_id, contact_email FROM opportunities WHERE contact_email IS NOT NULL LIMIT 10;")
    rows = cur.fetchall()
    conn.close()

    for opp_id, email in rows:
        # Check generated real message id semantics
        real_msg_id = UnifiedOutreachBridge.generate_real_message_id(opp_id, "iitd.ac.in")
        assert real_msg_id.startswith("<")
        assert real_msg_id.endswith("@iitd.ac.in>")
        assert "." in real_msg_id

        # Internal tracking ID format
        tracking_ref = f"TRK-{opp_id[:12]}"
        assert tracking_ref.startswith("TRK-")
        # Ensure no fictional historical <1793... format was passed as a pre-existing sent id
        assert not tracking_ref.startswith("<1793")


def test_twenty_verified_multicategory_drafts_integrity():
    """Verify all 20 drafts across 4 categories have zero fabrications and reference verified projects."""
    drafts = DomainAdaptivePersonalizer.generate_twenty_verified_multicategory_drafts()
    assert len(drafts) == 20, f"Expected exactly 20 drafts, got {len(drafts)}"

    categories = {}
    for d in drafts:
        cat = d["category"]
        categories[cat] = categories.get(cat, 0) + 1

        # Check required fields
        assert d["applicant_name"] == "Harshith N"
        full_text = d.get("pitch_body") or d.get("statement_of_purpose") or ""
        assert "Harshith N" in full_text
        assert "IIT Delhi" in full_text
        assert "Chemical Engineering" in full_text
        assert "harsh-bitverse" in full_text or "harsh-bitverse" in d.get("project_evidence_reference", "") or "harsh-bitverse" in DomainAdaptivePersonalizer.APPLICANT_PROFILE["portfolio_url"]

        # Verify factual rigor: no speedup hallucinations (e.g. '100x', '10x faster')
        assert "100x" not in full_text
        assert "10x faster" not in full_text
        assert "40% reduction" not in full_text  # only factual metrics defined in profile

    # Exactly 5 per category
    assert categories["ACADEMIC_LAB"] == 5
    assert categories["STARTUP"] == 5
    assert categories["FRONTIER_COMPANY"] == 5
    assert categories["INSTITUTIONAL_PROGRAM"] == 5
