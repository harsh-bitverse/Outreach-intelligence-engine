"""
Tests for Session 12 Reconciliation Audit and Mutual Exclusivity Invariants.
"""

import sqlite3
import pytest
from pathlib import Path

from src.opportunities.reconciliation_audit import ReconciliationAuditor
from src.opportunities.contact_verifier import ContactVerifier
from src.opportunities.unified_queue import UnifiedOutreachQueueManager
from src.opportunities.models import ContactChannel, ContactHistoryStatus


def test_reconciliation_mathematical_partition():
    """Verify that every portfolio opportunity is accounted for in exactly one mutually exclusive status."""
    auditor = ReconciliationAuditor()
    res = auditor.run_full_reconciliation()

    total = res["total_opportunities"]
    assert total >= 1802  # Expanded to 1,817 in Session 12

    history_statuses = res["by_history_status"]
    assert None not in history_statuses  # Zero unassigned / null statuses
    assert sum(history_statuses.values()) == total

    # Discrepancy delta must be exactly 0
    invariants = res["mathematical_invariants"]
    assert invariants["discrepancy_delta"] == 0
    assert invariants["partition_is_exact"] is True


def test_actionable_leads_reconciliation():
    """Verify that genuinely new actionable leads partition into verified academic + startups."""
    auditor = ReconciliationAuditor()
    res = auditor.run_full_reconciliation()

    eligible = res["eligible_partition"]
    assert eligible["genuinely_new_academic"] >= 148
    assert eligible["verified_startups"] >= 49
    assert eligible["actionable_leads_sum"] == eligible["genuinely_new_academic"] + eligible["verified_startups"]


def test_five_record_discrepancy_resolution():
    """Verify that the 5-record discrepancy (1,802 vs 1,807) is formally resolved."""
    auditor = ReconciliationAuditor()
    res = auditor.run_full_reconciliation()

    res_5 = res["discrepancy_resolutions"]["five_record_resolution"]
    assert len(res_5["double_counted_records"]) == 5
    assert "Orthogonal dimension double-counting" in res_5["explanation"]


def test_nine_lead_discrepancy_resolution():
    """Verify that the 9-lead discrepancy (197 vs 206) is formally resolved and bridged."""
    auditor = ReconciliationAuditor()
    res = auditor.run_full_reconciliation()

    res_9 = res["discrepancy_resolutions"]["nine_lead_resolution"]
    assert res_9["bridge_difference"] == 9
    assert res_9["strict_actionable_leads"] == res["eligible_partition"]["actionable_leads_sum"]
    assert res_9["loosely_enqueued_leads"] == res_9["strict_actionable_leads"] + 9
    assert len(res_9["bridge_record_ids"]) == 9
    assert "Production campaign ownership preservation" in res_9["explanation"]


def test_production_enrolled_professors_suppressed_from_queue():
    """Verify that professors enrolled in production are suppressed from the dev unified queue."""
    verifier = ContactVerifier()
    # Prof. Wojciech Matusik (in production with AWAITING_DRAFT)
    res_matusik = verifier.verify_contact(
        email="wojciech@csail.mit.edu",
        full_name="Prof. Wojciech Matusik",
        org_name="MIT CSAIL"
    )
    assert res_matusik["is_dispatch_eligible"] is False
    assert res_matusik["classification"] == "ALREADY_CONTACTED"
    assert "PRODUCTION_CAMPAIGN_OWNED" in res_matusik["reason"]

    # Prof. Matei Zaharia (in production with SENT - DELIVERY UNCONFIRMED)
    res_zaharia = verifier.verify_contact(
        email="matei@cs.berkeley.edu",
        full_name="Matei Zaharia",
        org_name="UC Berkeley"
    )
    assert res_zaharia["is_dispatch_eligible"] is False
    assert res_zaharia["classification"] == "ALREADY_CONTACTED"
