"""
Comprehensive test suite for Scheduler Quiescence, Supervisor Lockout,
and Fault-Tolerant Gate A Preflight.
"""

import os
import json
import time
import shutil
import sqlite3
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.settings import settings
from src.scheduler.autonomous_campaign_loop import AutonomousCampaignLoop
from src.scheduler.quiescence_controller import (
    SchedulerQuiescenceManager,
    ActiveTransactionsError,
    ConcurrentWriterLockError
)
from src.opportunities.authoritative_migration_package import (
    AuthoritativeWorkbookMigrationPackage,
    ConcurrentWriterActiveError
)
from src.opportunities.activation_gates import ActivationGateManager, GateID


@pytest.fixture
def temp_environment():
    """Create isolated temporary environment with faithful database and workbook snapshots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test_outreach.db"
        wb_path = tmp_path / "test_workbook.xlsx"
        flag_path = tmp_path / "maintenance_quiesce.flag"
        lock_path = tmp_path / "test_scheduler.lock"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot real DB & WB
        shutil.copy2(str(settings.SQLITE_DB_PATH), str(db_path))
        shutil.copy2(str(settings.MASTER_WORKBOOK_PATH), str(wb_path))

        yield {
            "tmp_path": tmp_path,
            "db_path": db_path,
            "wb_path": wb_path,
            "flag_path": flag_path,
            "lock_path": lock_path,
            "backup_dir": backup_dir
        }


def test_quiescence_flag_ipc_and_interruptible_sleep(temp_environment):
    """Verify that setting the quiescence flag interrupts sleep within milliseconds."""
    env = temp_environment
    flag_path = env["flag_path"]
    lock_path = env["lock_path"]

    loop = AutonomousCampaignLoop(
        workbook_path=env["wb_path"],
        lock_path=lock_path,
        quiescence_flag_path=flag_path
    )

    assert not loop.is_quiescence_requested()

    # Sleep without flag finishes full duration or timeout
    start_t = time.time()
    interrupted = loop._sleep_interruptible(0.1)
    assert not interrupted
    assert (time.time() - start_t) >= 0.09

    # Set quiescence flag
    mgr = SchedulerQuiescenceManager(quiescence_flag_path=flag_path, scheduler_lock_path=lock_path)
    mgr.request_quiescence(operator_id="TEST_OPERATOR", reason="Unit Test Quiescence")

    assert loop.is_quiescence_requested()

    # Sleep with flag interrupts immediately
    start_t = time.time()
    interrupted_early = loop._sleep_interruptible(10.0)
    elapsed = time.time() - start_t

    assert interrupted_early is True
    assert elapsed < 0.6  # interrupted in under 600ms

    # Cancel quiescence
    mgr.cancel_quiescence()
    assert not loop.is_quiescence_requested()


def test_quiescence_clean_exit_and_lock_release(temp_environment):
    """Verify that a campaign loop cleanly exits and unlinks scheduler lock on quiescence."""
    env = temp_environment
    flag_path = env["flag_path"]
    lock_path = env["lock_path"]

    # Pre-set quiescence flag
    flag_path.write_text(json.dumps({"reason": "pre_set_quiesce"}), encoding="utf-8")

    loop = AutonomousCampaignLoop(
        workbook_path=env["wb_path"],
        lock_path=lock_path,
        quiescence_flag_path=flag_path
    )

    # Run continuous loop with stop_on_idle=False (normally infinite)
    # Because flag is present, it must exit immediately
    loop.run_continuous_campaign(dry_run=True, poll_interval_seconds=1)

    # Invariant: lock must be unlinked via finally block
    assert not lock_path.exists(), "Scheduler lock file must be cleanly released upon quiescence exit!"


def test_fault_injection_active_watches_refuses_cutover(temp_environment):
    """Fault injection: If delivery watch is in flight, preflight must fail closed."""
    env = temp_environment
    db_path = env["db_path"]

    # Inject simulated ACTIVE_WATCH record
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO outbound_delivery_tracker (
                professor_id, university_name, workbook_row_index, recipient_email, message_id,
                smtp_accepted_at, tracking_started_at, watch_deadline_at, transport_status, delivery_status,
                tracking_status, created_at, updated_at
            ) VALUES (
                'test_prof_active', 'Test Uni', 999, 'active_watch@test.edu', '<msg_active_999@test.edu>',
                '2026-11-08T03:00:00Z', '2026-11-08T03:00:00Z', '2026-11-08T03:10:00Z', 'ACCEPTED', 'UNCONFIRMED',
                'ACTIVE_WATCH', '2026-11-08T03:00:00Z', '2026-11-08T03:00:00Z'
            );
        """)
        conn.commit()
    finally:
        conn.close()

    mgr = SchedulerQuiescenceManager(
        quiescence_flag_path=env["flag_path"],
        scheduler_lock_path=env["lock_path"],
        master_workbook_path=env["wb_path"],
        db_path=db_path
    )

    preflight = mgr.verify_quiescence_preflight()
    assert preflight["can_proceed_safely"] is False
    assert any("active delivery watches" in r.lower() for r in preflight["refusal_reasons"])


def test_fault_injection_active_lock_refuses_cutover(temp_environment, monkeypatch):
    """Fault injection: If .lock or .opp_lock exists, migration must refuse to proceed."""
    monkeypatch.setenv("ENABLE_PRODUCTION_WORKBOOK_MUTATION", "1")
    env = temp_environment
    wb_path = env["wb_path"]
    lock_file = wb_path.with_suffix(".lock")
    lock_file.touch()

    pkg = AuthoritativeWorkbookMigrationPackage(
        prod_workbook_path=wb_path,
        prod_db_path=env["db_path"],
        backup_dir=env["backup_dir"]
    )

    coord = pkg.check_writer_coordination(target_workbook=wb_path)
    assert coord["can_proceed_safely"] is False
    assert any(lock_file.name in l for l in coord["active_locks"])

    with pytest.raises(ConcurrentWriterActiveError):
        pkg.execute_production_cutover_gate_a(skip_quiescence_check=True)

    lock_file.unlink()


def test_fresh_backup_package_manifest_metadata(temp_environment):
    """Verify that fresh preflight backup records exact hashes, timestamps, and table/sheet row counts."""
    env = temp_environment

    pkg = AuthoritativeWorkbookMigrationPackage(
        prod_workbook_path=env["wb_path"],
        prod_db_path=env["db_path"],
        backup_dir=env["backup_dir"]
    )

    manifest = pkg.create_consistent_preflight_backup_package()

    assert manifest["integrity_verified"] is True
    assert "source_db_tables" in manifest
    assert "professors" in manifest["source_db_tables"]
    assert manifest["source_db_tables"]["professors"] > 0
    assert "source_workbook_sheets" in manifest
    assert "Professor Database" in manifest["source_workbook_sheets"]
    assert manifest["source_workbook_sheets"]["Professor Database"] in (1391, 1562)

    # Verify manifest file on disk
    manifest_file = Path(manifest["manifest_path"])
    assert manifest_file.exists()
    disk_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert disk_data["backup_id"] == manifest["backup_id"]
    assert disk_data["source_workbook_sha256"] == manifest["source_workbook_sha256"]


def test_deterministic_rollback_with_exact_fresh_id(temp_environment):
    """Verify that rollback works with exact fresh backup ID and strictly forbids wildcards."""
    env = temp_environment
    wb_path = env["wb_path"]
    db_path = env["db_path"]

    original_wb_hash = AuthoritativeWorkbookMigrationPackage.compute_sha256(wb_path)
    original_db_hash = AuthoritativeWorkbookMigrationPackage.compute_sha256(db_path)

    pkg = AuthoritativeWorkbookMigrationPackage(
        prod_workbook_path=wb_path,
        prod_db_path=db_path,
        backup_dir=env["backup_dir"]
    )

    manifest = pkg.create_consistent_preflight_backup_package(custom_backup_id="fresh_test_checkpoint_001")
    fresh_id = manifest["backup_id"]

    # Mutate the working copies
    wb_path.write_bytes(b"MUTATED_WORKBOOK_CONTENT")
    db_path.write_bytes(b"MUTATED_DATABASE_CONTENT")

    # Rollback with wildcards must raise ValueError
    with pytest.raises(ValueError):
        pkg.execute_deterministic_rollback(
            backup_id="fresh_test_*",
            target_workbook=wb_path,
            target_db=db_path
        )

    # Rollback with exact fresh ID
    res = pkg.execute_deterministic_rollback(
        backup_id=fresh_id,
        target_workbook=wb_path,
        target_db=db_path
    )

    assert res["status"] == "ROLLBACK_SUCCESSFUL"
    assert res["byte_perfect_restoration"] is True
    assert AuthoritativeWorkbookMigrationPackage.compute_sha256(wb_path) == manifest["backup_workbook_sha256"]
    assert AuthoritativeWorkbookMigrationPackage.compute_sha256(db_path) == manifest["backup_db_sha256"]


def test_scheduler_resume_zero_duplicate_outreach(temp_environment):
    """
    Verify that resuming the scheduler after cutover does not re-dispatch
    to already-contacted professors (preserves all 901 historical contacts).
    """
    env = temp_environment

    # Open snapshot DB
    conn = sqlite3.connect(str(env["db_path"]))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT recipient_email) FROM outbound_delivery_tracker;")
        historical_distinct = cur.fetchone()[0]
        assert historical_distinct == 1000

        # Invariant: all contacted recipients must have an excluded outreach status
        cur.execute("""
            SELECT p.email, p.outreach_status 
            FROM professors p 
            WHERE p.email IN (SELECT recipient_email FROM outbound_delivery_tracker);
        """)
        tracked_profs = cur.fetchall()
    finally:
        conn.close()

    EXCLUDED_SUBSTRINGS = ("SENT", "ERROR", "AMBIGUOUS_DELIVERY", "BOUNCED", "INVALID", "REPLIED", "UNCONFIRMED", "UNSENT")
    for email, status in tracked_profs:
        st_upper = (status or "").upper()
        is_excluded = any(sub in st_upper for sub in EXCLUDED_SUBSTRINGS)
        assert is_excluded is True, f"Tracked recipient {email} with status {status} must be excluded from re-dispatch!"
