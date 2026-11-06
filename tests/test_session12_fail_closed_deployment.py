"""
Tests for Session 12 Fail-Closed Deployment Manager and Fault Injection Safeguards.
"""

import os
import shutil
import sqlite3
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from src.opportunities.deployment_manager import (
    UnifiedDeploymentManager,
    DeploymentAbortedError,
    ActiveSMTPTransactionTimeoutError,
    SchedulerLockOccupiedError,
    CheckpointCreationError,
    RollbackVerificationError,
    DatabaseContentionError
)


@pytest.fixture
def sandbox_env():
    """Creates an isolated temporary sandbox with mock SQLite and workbook files."""
    tmpdir = tempfile.mkdtemp()
    tmppath = Path(tmpdir)

    mock_db = tmppath / "test_prod.db"
    conn = sqlite3.connect(str(mock_db))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE professors (
            professor_id TEXT PRIMARY KEY,
            full_name TEXT,
            email TEXT,
            outreach_status TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE outbound_delivery_tracker (
            tracker_id TEXT PRIMARY KEY,
            recipient_email TEXT,
            tracking_status TEXT,
            smtp_accepted_at TEXT,
            dispatched_at TEXT
        );
    """)
    cur.execute("INSERT INTO professors VALUES ('prof_1', 'Test Prof', 'prof@uni.edu', 'NEW');")
    conn.commit()
    conn.close()

    mock_wb = tmppath / "test_master.xlsx"
    mock_wb.write_bytes(b"PK\x03\x04MockExcelWorkbookBytesForTesting")

    mock_lock = tmppath / "scheduler.lock"
    mock_chk = tmppath / "checkpoints"

    manager = UnifiedDeploymentManager(
        prod_db_path=mock_db,
        master_workbook_path=mock_wb,
        lock_file_path=mock_lock,
        backup_dir=mock_chk
    )

    yield {
        "dir": tmppath,
        "db": mock_db,
        "wb": mock_wb,
        "lock": mock_lock,
        "chk": mock_chk,
        "manager": manager
    }

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_fail_closed_on_active_smtp_transaction(sandbox_env):
    """Verifies that in-flight SMTP dispatch causes fail-closed abort."""
    manager = sandbox_env["manager"]
    mock_db = sandbox_env["db"]

    # Insert an active transaction within last 10 seconds
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(mock_db))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO outbound_delivery_tracker VALUES ('trk_1', 'lead@test.com', 'ACTIVE_WATCH', ?, ?);",
        (now_iso, now_iso)
    )
    conn.commit()
    conn.close()

    # Must raise ActiveSMTPTransactionTimeoutError when fail_closed=True
    with pytest.raises(ActiveSMTPTransactionTimeoutError) as exc_info:
        manager.wait_for_active_smtp_transaction(max_wait_seconds=1, poll_interval=0.2, fail_closed=True)

    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)
    assert issubclass(ActiveSMTPTransactionTimeoutError, DeploymentAbortedError)


def test_clean_wait_when_no_active_transaction(sandbox_env):
    """Verifies that clean wait succeeds when no transactions are active."""
    manager = sandbox_env["manager"]
    clear, msg = manager.wait_for_active_smtp_transaction(max_wait_seconds=1, poll_interval=0.2, fail_closed=True)
    assert clear is True
    assert msg == "CLEAR_NO_ACTIVE_TRANSACTIONS"


def test_fail_closed_on_occupied_scheduler_lock(sandbox_env):
    """Verifies that occupied lockfile held by active process triggers fail-closed abort."""
    manager = sandbox_env["manager"]
    mock_lock = sandbox_env["lock"]

    # Write current running PID to lockfile
    mock_lock.write_text(str(os.getpid()), encoding="utf-8")

    with pytest.raises(SchedulerLockOccupiedError) as exc_info:
        manager.verify_single_instance_lock(fail_closed=True)

    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)
    assert str(os.getpid()) in str(exc_info.value)


def test_free_lock_passes_verification(sandbox_env):
    """Verifies that absence of lockfile passes check cleanly."""
    manager = sandbox_env["manager"]
    res = manager.verify_single_instance_lock(fail_closed=True)
    assert res["lock_held"] is False
    assert res["process_running"] is False


def test_fail_closed_on_checkpoint_failure(sandbox_env):
    """Verifies that checkpoint failure aborts deployment."""
    manager = sandbox_env["manager"]
    # Point manager to non-existent unreadable db
    manager.prod_db_path = Path("/nonexistent/invalid_path/outreach.db")

    # Should raise CheckpointCreationError when fail_closed=True
    # (or succeeds if path doesn't exist, so let's point to an unreadable file or locked directory)
    corrupt_db = sandbox_env["dir"] / "corrupt.db"
    corrupt_db.write_text("NOT A VALID SQLITE DB", encoding="utf-8")
    manager.prod_db_path = corrupt_db

    with pytest.raises(CheckpointCreationError) as exc_info:
        manager.create_pre_deployment_checkpoint(checkpoint_tag="FAIL_SNAP", fail_closed=True)

    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)


def test_fail_closed_on_rollback_verification_failure(sandbox_env):
    """Verifies that rollback from missing or corrupted checkpoint aborts."""
    manager = sandbox_env["manager"]
    empty_chk = sandbox_env["dir"] / "empty_chk"
    empty_chk.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RollbackVerificationError) as exc_info:
        manager.rollback_to_checkpoint(
            checkpoint_dir=empty_chk,
            target_db_path=sandbox_env["db"],
            fail_closed=True
        )

    assert "DEPLOYMENT_ABORTED" in str(exc_info.value)


def test_full_preflight_gate_clean_and_abort(sandbox_env):
    """Tests execute_deployment_preflight under normal and occupied states."""
    manager = sandbox_env["manager"]

    # Clean preflight
    res = manager.execute_deployment_preflight(fail_closed=True)
    assert res["preflight_status"] == "READY_FOR_DEPLOYMENT"

    # Occupy lock -> must abort
    sandbox_env["lock"].write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(SchedulerLockOccupiedError):
        manager.execute_deployment_preflight(fail_closed=True)
