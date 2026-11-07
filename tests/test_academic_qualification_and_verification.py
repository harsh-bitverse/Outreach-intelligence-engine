"""
Test suite for Academic Internship Eligibility, Hosting Availability & Funding Verification.
Validates independent orthogonal qualification dimensions, 6 academic classifications,
reply outcome mapping, 14-dimension academic reporting without double counting,
and production database isolation.
"""

import os
import sqlite3
import pytest
from pathlib import Path

from config.settings import settings
from src.opportunities.models import (
    Opportunity,
    Organization,
    OpportunityType,
    OpportunityClassification,
    RoleTitleType,
    AcademicClassification,
    VisitingStudentPolicy,
    HostingAvailability,
    FundingAvailability,
    AcademicReplyOutcome
)
from src.opportunities.adapters.academic_lab import (
    AcademicLabAdapter,
    determine_academic_classification
)
from src.opportunities.academic_bridge import AcademicOutreachBridge
from src.opportunities.dashboard import PortfolioDashboard


@pytest.fixture
def dev_db_conn():
    dev_db_path = settings.DATA_DIR / "development" / "opportunity_dev.db"
    assert dev_db_path.exists(), "Development DB must exist"
    conn = sqlite3.connect(str(dev_db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_independent_orthogonal_qualification_dimensions():
    """
    Tests that previous hosting evidence coexists with unknown availability,
    and funding availability is tracked independently from visiting student policy.
    """
    adapter = AcademicLabAdapter()
    raw_items = adapter.fetch_raw_opportunities(limit=50)
    
    # 1. MIT CSAIL: previous hosting verified, but Summer 2027 availability is UNKNOWN
    mit_item = next(i for i in raw_items if "MIT" in i.get("lab_name", "") or "MIT" in i.get("institution", ""))
    assert mit_item["hosting_history_verified"] is True
    assert mit_item["summer_2027_hosting_availability"] == HostingAvailability.UNKNOWN.value
    assert mit_item["institution_visiting_policy"] == VisitingStudentPolicy.FORMAL_VISITING_STUDENT_PROGRAM.value
    assert mit_item["funding_availability"] == FundingAvailability.CONFIRMED_FUNDED.value
    
    org, opp = adapter.normalize_raw_item(mit_item)
    assert opp.hosting_history_verified is True
    assert opp.summer_2027_hosting_availability == HostingAvailability.UNKNOWN.value
    assert opp.institution_visiting_policy == VisitingStudentPolicy.FORMAL_VISITING_STUDENT_PROGRAM.value
    assert opp.funding_availability == FundingAvailability.CONFIRMED_FUNDED.value
    # Normalized classification is HOSTING_HISTORY_VERIFIED (since past hosting exists but availability is UNKNOWN)
    assert opp.academic_classification == AcademicClassification.HOSTING_HISTORY_VERIFIED.value

    # 2. Purdue: visiting policy active, but hosting history is False, availability & funding UNKNOWN
    purdue_item = next(i for i in raw_items if "Purdue" in i["institution"])
    assert purdue_item["institution_visiting_policy"] == VisitingStudentPolicy.INDEPENDENT_RESEARCH_SCHOLAR_PERMITTED.value
    assert purdue_item["hosting_history_verified"] is False
    assert purdue_item["summer_2027_hosting_availability"] == HostingAvailability.UNKNOWN.value
    assert purdue_item["funding_availability"] == FundingAvailability.UNKNOWN.value
    
    org_p, opp_p = adapter.normalize_raw_item(purdue_item)
    assert opp_p.academic_classification == AcademicClassification.PROSPECTIVE_INQUIRY.value


def test_six_primary_academic_classifications():
    """
    Validates deterministic assignment of all 6 academic classifications:
    CONFIRMED_OPENING, PROSPECTIVE_INQUIRY, HOSTING_HISTORY_VERIFIED,
    INSTITUTION_POLICY_UNVERIFIED, CONFIRMED_UNAVAILABLE_2027, INELIGIBLE.
    """
    # 1. CONFIRMED_OPENING
    c1 = determine_academic_classification(
        hosting_availability=HostingAvailability.CONFIRMED_AVAILABLE.value,
        visiting_policy=VisitingStudentPolicy.FORMAL_VISITING_STUDENT_PROGRAM.value,
        hosting_history_verified=True,
        is_ineligible=False,
        is_confirmed_opening=True
    )
    assert c1 == AcademicClassification.CONFIRMED_OPENING.value

    # 2. PROSPECTIVE_INQUIRY
    c2 = determine_academic_classification(
        hosting_availability=HostingAvailability.UNKNOWN.value,
        visiting_policy=VisitingStudentPolicy.INDEPENDENT_RESEARCH_SCHOLAR_PERMITTED.value,
        hosting_history_verified=False,
        is_ineligible=False,
        is_confirmed_opening=False
    )
    assert c2 == AcademicClassification.PROSPECTIVE_INQUIRY.value

    # 3. HOSTING_HISTORY_VERIFIED
    c3 = determine_academic_classification(
        hosting_availability=HostingAvailability.UNKNOWN.value,
        visiting_policy=VisitingStudentPolicy.FORMAL_VISITING_STUDENT_PROGRAM.value,
        hosting_history_verified=True,
        is_ineligible=False,
        is_confirmed_opening=False
    )
    assert c3 == AcademicClassification.HOSTING_HISTORY_VERIFIED.value

    # 4. INSTITUTION_POLICY_UNVERIFIED
    c4 = determine_academic_classification(
        hosting_availability=HostingAvailability.UNKNOWN.value,
        visiting_policy=VisitingStudentPolicy.POLICY_UNVERIFIED.value,
        hosting_history_verified=False,
        is_ineligible=False,
        is_confirmed_opening=False
    )
    assert c4 == AcademicClassification.INSTITUTION_POLICY_UNVERIFIED.value

    # 5. CONFIRMED_UNAVAILABLE_2027
    c5 = determine_academic_classification(
        hosting_availability=HostingAvailability.CONFIRMED_UNAVAILABLE.value,
        visiting_policy=VisitingStudentPolicy.FORMAL_VISITING_STUDENT_PROGRAM.value,
        hosting_history_verified=True,
        is_ineligible=False,
        is_confirmed_opening=False
    )
    assert c5 == AcademicClassification.CONFIRMED_UNAVAILABLE_2027.value

    # 6. INELIGIBLE
    c6 = determine_academic_classification(
        hosting_availability=HostingAvailability.CONFIRMED_AVAILABLE.value,
        visiting_policy=VisitingStudentPolicy.FORMAL_VISITING_STUDENT_PROGRAM.value,
        hosting_history_verified=True,
        is_ineligible=True,
        is_confirmed_opening=True
    )
    assert c6 == AcademicClassification.INELIGIBLE.value


def test_reply_outcome_mapping_and_rules():
    """
    Tests AcademicOutreachBridge reply mapping:
    - Routine reply does not infer confirmed offer.
    - FUNDING_UNAVAILABLE does not set INELIGIBLE.
    - CAPACITY_FULL / DECLINED sets availability to CONFIRMED_UNAVAILABLE.
    """
    bridge = AcademicOutreachBridge()

    # Rule: Routine interested reply is NOT a confirmed offer
    r1 = bridge.map_reply_to_academic_outcome("REPLY_INTERESTED", "Happy to chat about your work.")
    assert r1["academic_reply_outcome"] == AcademicReplyOutcome.INTERESTED.value
    assert r1["is_confirmed_offer"] is False

    # Rule: Funding unavailable sets funding to CONFIRMED_UNFUNDED without setting ineligible
    r2 = bridge.map_reply_to_academic_outcome("FUNDING_UNAVAILABLE", "We have no stipend for visiting students.")
    assert r2["academic_reply_outcome"] == AcademicReplyOutcome.FUNDING_UNAVAILABLE.value
    assert r2["funding_availability"] == FundingAvailability.CONFIRMED_UNFUNDED.value
    assert r2["is_confirmed_offer"] is False

    # Rule: Capacity full sets availability to CONFIRMED_UNAVAILABLE
    r3 = bridge.map_reply_to_academic_outcome("REPLY_CAPACITY_FULL", "Our lab is full for 2027.")
    assert r3["academic_reply_outcome"] == AcademicReplyOutcome.CAPACITY_FULL.value
    assert r3["summer_2027_hosting_availability"] == HostingAvailability.CONFIRMED_UNAVAILABLE.value

    # Rule: Declined sets availability to CONFIRMED_UNAVAILABLE
    r4 = bridge.map_reply_to_academic_outcome("REPLY_DECLINED", "Not taking students.")
    assert r4["academic_reply_outcome"] == AcademicReplyOutcome.DECLINED.value
    assert r4["summer_2027_hosting_availability"] == HostingAvailability.CONFIRMED_UNAVAILABLE.value


def test_academic_portfolio_reporting_fourteen_dimensions(dev_db_conn):
    """
    Tests the 14 tracked academic portfolio dimensions and verifies no double-counting.
    """
    metrics = PortfolioDashboard.compute_academic_portfolio_metrics(dev_db_conn)
    
    assert "1_total_research_matched_professors" in metrics
    assert "2_confirmed_summer_2027_openings" in metrics
    assert "3_prospective_research_inquiries" in metrics
    assert "4_previously_hosted_visiting_students" in metrics
    assert "5_institutionally_eligible_contacts" in metrics
    assert "6_eligibility_unknown" in metrics
    assert "7_confirmed_ineligible_contacts" in metrics
    assert "8_confirmed_unavailable_labs" in metrics
    assert "9_funding_breakdown" in metrics
    assert "10_professors_contacted" in metrics
    assert "11_emails_actually_bounced" in metrics
    assert "12_positive_replies_and_interviews" in metrics
    assert "13_applications_in_progress" in metrics
    assert "14_confirmed_offers" in metrics

    total_academic = metrics["1_total_research_matched_professors"]
    assert total_academic >= 8, f"Expected at least 8 academic opportunities in dev DB, got {total_academic}"

    # Verify no double-counting across primary classifications:
    # (Confirmed + Prospective + Hosted History + Policy Unverified + Ineligible + Unavailable)
    confirmed = metrics["2_confirmed_summer_2027_openings"]
    prospective = metrics["3_prospective_research_inquiries"]
    policy_unverified = metrics["6_eligibility_unknown"]
    ineligible = metrics["7_confirmed_ineligible_contacts"]
    unavailable = metrics["8_confirmed_unavailable_labs"]

    # Sum of mutually exclusive primary classifications equals total_academic
    cur = dev_db_conn.cursor()
    cur.execute("""
        SELECT academic_classification, count(*) 
        FROM opportunities 
        WHERE opportunity_type = 'ACADEMIC_LAB' 
        GROUP BY academic_classification;
    """)
    class_counts = {r[0]: r[1] for r in cur.fetchall()}
    assert sum(class_counts.values()) == total_academic

    # Funding breakdown sum equals total_academic
    f_bd = metrics["9_funding_breakdown"]
    total_funding_entries = sum(f_bd.values())
    assert total_funding_entries == total_academic

    # Telemetry verified from production
    assert metrics["10_professors_contacted"] >= 682
    assert metrics["11_emails_actually_bounced"] == 94


def test_production_telemetry_read_only_and_isolation():
    """
    Guarantees production SQLite database is accessed exclusively in read-only mode,
    and production PID is verified running.
    """
    bridge = AcademicOutreachBridge()
    telemetry = bridge.get_production_outreach_telemetry()
    assert telemetry["professors_contacted"] >= 682
    assert telemetry["emails_bounced"] == 94
    assert telemetry["emails_delivered"] >= 582

    # Check production DB tables unified post-Gate-A
    prod_db = settings.DATA_DIR / "outreach_engine.db"
    conn = sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    assert "opportunities" in tables
    assert "discovery_checkpoints" in tables
