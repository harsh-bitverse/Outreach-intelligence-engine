"""
Session 11: Reversible Deployment Rehearsal and Safety Validation Tests.

Validates the 5 Workstream C mandatory requirements:
1. Active SMTP transaction detection & clean wait.
2. Single-instance lock verification & duplicate instance prevention.
3. Checkpoint preservation of live state (database snapshot & master workbook).
4. Idempotent metadata migration (zero disruption to existing tables).
5. Rollback procedure verification (instant revert to pre-migration state).
"""

import os
import shutil
import sqlite3
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timezone

from src.opportunities.deployment_manager import UnifiedDeploymentManager
from src.database.connection import initialize_database


@pytest.fixture
def isolated_deployment_env():
    """Creates an isolated temporary test sandbox replicating production files."""
    tmpdir = tempfile.mkdtemp(prefix="deploy_test_")
    tmppath = Path(tmpdir)

    # 1. Mock production database with existing tables
    mock_prod_db = tmppath / "mock_prod.db"
    conn = sqlite3.connect(str(mock_prod_db))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE professors (
            professor_id TEXT PRIMARY KEY,
            university_name TEXT,
            country TEXT,
            full_name TEXT,
            email TEXT,
            outreach_status TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE outreach_queue (
            queue_id TEXT PRIMARY KEY,
            professor_id TEXT,
            draft_id TEXT,
            time_block_id TEXT,
            window_start_ist TEXT,
            window_end_ist TEXT,
            sequence_order INTEGER,
            status TEXT,
            attempts INTEGER DEFAULT 0,
            last_attempt_at TEXT,
            error_message TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE outbound_delivery_tracker (
            tracker_id TEXT PRIMARY KEY,
            professor_id TEXT,
            recipient_email TEXT,
            block_id TEXT,
            smtp_message_id TEXT,
            dispatched_at TEXT,
            watch_status TEXT,
            final_status TEXT
        );
    """)
    # Insert mock records
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        INSERT INTO professors (professor_id, university_name, country, full_name, email, outreach_status, created_at, updated_at)
        VALUES ('prof_1', 'MIT', 'USA', 'Prof One', 'one@mit.edu', 'SENT', ?, ?);
    """, (now_iso, now_iso))
    conn.commit()
    conn.close()

    # 2. Mock master workbook
    mock_wb = tmppath / "mock_master.xlsx"
    mock_wb.write_bytes(b"PK\x03\x04MockExcelZipContent")

    # 3. Mock lockfile
    mock_lock = tmppath / "scheduler.lock"

    # 4. Checkpoints folder
    mock_chk_dir = tmppath / "checkpoints"

    manager = UnifiedDeploymentManager(
        prod_db_path=mock_prod_db,
        master_workbook_path=mock_wb,
        lock_file_path=mock_lock,
        backup_dir=mock_chk_dir
    )

    yield {
        "manager": manager,
        "tmpdir": tmppath,
        "db": mock_prod_db,
        "wb": mock_wb,
        "lock": mock_lock,
        "chk_dir": mock_chk_dir
    }

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_step1_active_smtp_transaction_detection(isolated_deployment_env):
    """Step 1: Test detection of active SMTP transactions."""
    env = isolated_deployment_env
    manager = env["manager"]

    # Initially clear
    clear, msg = manager.wait_for_active_smtp_transaction(max_wait_seconds=1)
    assert clear is True
    assert msg == "CLEAR_NO_ACTIVE_TRANSACTIONS"

    # Insert an active in-flight dispatch
    conn = sqlite3.connect(str(env["db"]))
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO outbound_delivery_tracker (tracker_id, professor_id, recipient_email, block_id, smtp_message_id, dispatched_at, watch_status, final_status)
        VALUES ('tr_1', 'prof_1', 'one@mit.edu', 'BLOCK_09', '<msg1@iitd.ac.in>', datetime('now'), 'WATCHING', 'ACTIVE');
    """)
    conn.commit()
    conn.close()

    # Should detect busy transaction and timeout on short wait
    clear2, msg2 = manager.wait_for_active_smtp_transaction(max_wait_seconds=0.5, poll_interval=0.1)
    assert clear2 is False
    assert msg2 == "TIMEOUT_WAITING_FOR_ACTIVE_TRANSACTION"


def test_step2_single_instance_lock_verification(isolated_deployment_env):
    """Step 2: Test lockfile inspection and duplicate instance prevention."""
    env = isolated_deployment_env
    manager = env["manager"]

    # When lockfile doesn't exist
    res = manager.verify_single_instance_lock()
    assert res["lock_held"] is False

    # When lockfile contains non-running PID
    env["lock"].write_text("9999999", encoding="utf-8")
    res2 = manager.verify_single_instance_lock()
    assert res2["lock_held"] is True
    assert res2["lock_pid"] == 9999999
    assert res2["process_running"] is False

    # When lockfile contains current process PID
    env["lock"].write_text(str(os.getpid()), encoding="utf-8")
    res3 = manager.verify_single_instance_lock()
    assert res3["lock_held"] is True
    assert res3["lock_pid"] == os.getpid()
    assert res3["process_running"] is True


def test_step3_checkpoint_preservation(isolated_deployment_env):
    """Step 3: Test atomic durable checkpoint creation before deployment."""
    env = isolated_deployment_env
    manager = env["manager"]

    chk = manager.create_pre_deployment_checkpoint(checkpoint_tag="TEST_CHECKPOINT_01")
    assert chk["checkpoint_tag"] == "TEST_CHECKPOINT_01"
    assert Path(chk["checkpoint_dir"]).exists()
    assert Path(chk["db_backup_path"]).exists()
    assert Path(chk["workbook_backup_path"]).exists()

    # Verify backup database integrity
    chk_conn = sqlite3.connect(str(chk["db_backup_path"]))
    cur = chk_conn.cursor()
    cur.execute("SELECT count(*) FROM professors;")
    assert cur.fetchone()[0] == 1
    chk_conn.close()


def test_step4_idempotent_metadata_migration(isolated_deployment_env):
    """Step 4: Test idempotent metadata migration without affecting existing tables."""
    env = isolated_deployment_env
    manager = env["manager"]

    mig = manager.run_idempotent_metadata_migration(env["db"])
    assert mig["success"] is True
    assert "professors" in mig["pre_existing_tables_preserved"]
    assert "outreach_queue" in mig["pre_existing_tables_preserved"]
    assert "outbound_delivery_tracker" in mig["pre_existing_tables_preserved"]
    assert mig["unified_queue_created"] is True

    # Check database tables
    conn = sqlite3.connect(str(env["db"]))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM professors;")
    assert cur.fetchone()[0] == 1, "Professors table data was preserved"
    cur.execute("SELECT count(*) FROM unified_outreach_queue;")
    assert cur.fetchone()[0] == 0, "Unified queue was cleanly initialized empty"
    conn.close()

    # Re-running migration is strictly idempotent
    mig2 = manager.run_idempotent_metadata_migration(env["db"])
    assert mig2["success"] is True


def test_step5_rollback_procedure(isolated_deployment_env):
    """Step 5: Test instant rollback to pre-migration checkpoint."""
    env = isolated_deployment_env
    manager = env["manager"]

    # 1. Create checkpoint
    chk = manager.create_pre_deployment_checkpoint(checkpoint_tag="PRE_DEPLOY")
    chk_dir = Path(chk["checkpoint_dir"])

    # 2. Modify target database (simulate bad migration or state change)
    conn = sqlite3.connect(str(env["db"]))
    cur = conn.cursor()
    cur.execute("INSERT INTO professors (professor_id, full_name, email) VALUES ('prof_bad', 'Bad Record', 'bad@test.com');")
    conn.commit()
    conn.close()

    # Verify corrupt/modified state
    conn = sqlite3.connect(str(env["db"]))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM professors;")
    assert cur.fetchone()[0] == 2
    conn.close()

    # 3. Execute rollback
    rb = manager.rollback_to_checkpoint(
        checkpoint_dir=chk_dir,
        target_db_path=env["db"],
        target_wb_path=env["wb"]
    )
    assert rb["success"] is True

    # 4. Verify exact restored state
    conn = sqlite3.connect(str(env["db"]))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM professors;")
    assert cur.fetchone()[0] == 1, "Database rolled back cleanly to exact pre-deployment count"
    cur.execute("SELECT professor_id FROM professors;")
    assert cur.fetchone()[0] == "prof_1"
    conn.close()
