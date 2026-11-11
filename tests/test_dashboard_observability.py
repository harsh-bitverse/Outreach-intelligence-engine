"""
Unit and Integration Tests for Operations Observability Dashboard.
Verifies all 10 REST endpoints, real database metrics, worker health checks,
and zero interference with running scheduler PID 5136.
"""

import pytest
from fastapi.testclient import TestClient
from src.web.app import app
from src.web.routes.dashboard_service import dashboard_service, is_pid_alive


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_serve_dashboard_index(client):
    """Verify root URL serves the operator dashboard Single Page Application."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Live Operations Observability Dashboard" in response.text
    assert "IIT DELHI • OPPORTUNITY INTELLIGENCE ENGINE" in response.text


def test_dashboard_overview_kpis(client):
    """Verify overview endpoint returns consistent KPIs across all 4 engines."""
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200
    data = response.json()
    assert "kpis" in data
    kpis = data["kpis"]

    # Discovery KPIs
    assert kpis["discovery"]["total_qualified"] >= 1914
    assert kpis["discovery"]["active_checkpoints"] >= 5

    # Outreach KPIs
    assert kpis["outreach"]["accepted_today"] >= 0
    assert kpis["outreach"]["daily_quota"] == 200
    assert kpis["outreach"]["remaining_quota"] <= 200
    assert kpis["outreach"]["lifetime_accepted"] >= 1030

    # Portal KPIs
    assert kpis["portals"]["total_tracked"] == 269
    assert kpis["portals"]["submitted_live"] == 0
    assert kpis["portals"]["execution_guardrail"] == "FAIL_CLOSED_SIMULATION"

    # Outcomes KPIs
    assert kpis["outcomes"]["interviews_scheduled"] == 0
    assert "None Confirmed (0 Scheduled)" in kpis["outcomes"]["interview_lead"]
    assert kpis["outcomes"]["total_replies"] == 31

    # Excel Sync KPIs
    assert kpis["excel_sync"]["sheet_count"] == 18
    assert kpis["excel_sync"]["status"] == "UP_TO_DATE"


def test_dashboard_workers_health(client):
    """Verify all 5 worker heartbeats and execution states are accurately reported."""
    response = client.get("/api/dashboard/workers")
    assert response.status_code == 200
    data = response.json()
    workers = data["workers"]
    assert len(workers) >= 5

    worker_map = {w["worker_id"]: w for w in workers}
    
    # 1. SMTP Scheduler
    smtp = worker_map["worker_smtp_scheduler"]
    assert smtp["execution_mode"] == "continuous"
    assert isinstance(smtp["pid"], int) and smtp["pid"] > 0
    assert smtp["state"] == "RUNNING"
    assert is_pid_alive(smtp["pid"]) is True

    # 2. Discovery Engine
    disc = worker_map.get("worker_discovery_stream_a") or worker_map.get("worker_discovery_engine")
    assert disc is not None
    assert disc["state"] in ("SCHEDULED", "ACTIVE", "RUNNING")

    # 3. Portal Processor
    portal = worker_map["worker_portal_processor"]
    assert portal["execution_mode"] == "recurring"
    assert portal["state"] == "SCHEDULED"
    assert portal["records_submitted"] == 0

    # 4. IMAP Monitor
    imap = worker_map["worker_imap_monitor"]
    assert imap["execution_mode"] == "continuous"
    assert imap["state"] == "RUNNING"

    # 5. Workbook Sync
    sync = worker_map["worker_workbook_sync"]
    assert sync["sheet_count"] == 18
    assert sync["state"] == "IDLE"


def test_dashboard_discovery_pipeline(client):
    """Verify 5-stage discovery-to-production synchronization tracking."""
    response = client.get("/api/dashboard/discovery")
    assert response.status_code == 200
    data = response.json()

    assert data["metrics"]["lifetime_total_qualified"] >= 1914
    assert len(data["checkpoints"]) >= 5
    assert len(data["qs_coverage"]) >= 5

    pipeline = data["five_stage_pipeline"]
    assert pipeline["stage_1_discovered"]["count"] >= 1914
    assert pipeline["stage_2_verified"]["count"] == 1783
    assert pipeline["stage_3_inserted_sqlite"]["count"] == 1914
    assert pipeline["stage_4_synced_production_excel"]["count"] == 1914
    assert pipeline["stage_4_synced_production_excel"]["sheets"] == 18


def test_dashboard_outreach_queues(client):
    """Verify category queue breakdowns and daily budget constraints."""
    response = client.get("/api/dashboard/outreach")
    assert response.status_code == 200
    data = response.json()

    info = data["scheduler_info"]
    assert info["daily_budget"]["limit"] == 200
    assert info["daily_budget"]["consumed_today"] >= 0
    assert isinstance(info["shared_sender_pid"], int) and info["shared_sender_pid"] > 0

    assert len(data["next_10_scheduled"]) == 10
    assert len(data["recent_dispatches"]) == 20
    assert len(data["category_breakdown"]) >= 3


def test_dashboard_portals_fail_closed(client):
    """Verify 269 portal opportunities are tracked and live submissions remain fail-closed."""
    response = client.get("/api/dashboard/portals")
    assert response.status_code == 200
    data = response.json()

    funnel = data["funnel"]
    assert funnel["total_tracked"] == 269
    assert funnel["materials_prepared"] == 269
    assert funnel["form_fields_mapped"] == 269
    assert funnel["mock_only_prefill"] == 269
    assert funnel["confirmed_submitted"] == 0
    assert data["guardrail_status"]["live_submission_enabled"] is False


def test_dashboard_outcomes_positive_signals(client):
    """Verify 0 false interviews, 31 verified replies, and quarantined simulation."""
    response = client.get("/api/dashboard/outcomes")
    assert response.status_code == 200
    data = response.json()

    assert len(data["positive_engagement_queue"]) == 0
    assert len(data["verified_replies"]) == 31
    assert data["provenance_breakdown"]["VERIFIED_EMAIL"] == 31

    # Check quarantine record
    assert len(data["quarantined_claims"]) >= 1
    bodner_items = [q for q in data["quarantined_claims"] if "Bodner" in q["entity_name"]]
    assert len(bodner_items) >= 1
    q_item = bodner_items[0]
    assert "Bodner" in q_item["entity_name"]
    assert q_item["verification_status"] == "UNVERIFIED_QUARANTINED"
    assert q_item["item_type"] == "SIMULATED_TEST_FIXTURE"

    assert len(data["dsn_bounces"]) >= 1
    assert data["metrics"]["total_dsn_bounces"] >= 194


def test_dashboard_manual_actions(client):
    """Verify 3 prioritized human operator intervention tasks."""
    response = client.get("/api/dashboard/manual-actions")
    assert response.status_code == 200
    actions = response.json()
    assert len(actions) == 3

    action_ids = [a["action_id"] for a in actions]
    assert "ACT-PORTAL-001" in action_ids  # CERN
    assert "ACT-PORTAL-002" in action_ids  # DAAD
    assert "ACT-PORTAL-003" in action_ids  # Mitacs

    cern = next(a for a in actions if a["action_id"] == "ACT-PORTAL-001")
    assert "IIT Delhi" in cern["instructions"]
    assert cern["priority"] == "HIGH"


def test_dashboard_excel_parity(client):
    """Verify 18-sheet parity comparison against SQLite database."""
    response = client.get("/api/dashboard/excel-sync")
    assert response.status_code == 200
    data = response.json()

    assert data["sheet_count"] == 18
    assert data["status"] in ("UP_TO_DATE", "SYNC_IN_PROGRESS")
    assert data["single_writer_lock"] == "FREE"
    assert len(data["parity_comparison"]) == 12


def test_dashboard_alerts(client):
    """Verify real-time alerts include critical interview and manual action notices."""
    response = client.get("/api/dashboard/alerts")
    assert response.status_code == 200
    alerts = response.json()
    assert len(alerts) >= 3

    categories = [a["category"] for a in alerts]
    assert "OUTCOMES" in categories
    assert "PORTALS" in categories
    assert "OUTREACH" in categories
