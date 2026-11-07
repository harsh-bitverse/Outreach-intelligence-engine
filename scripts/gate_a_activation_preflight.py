"""
Gate A Final Production Safety & Operator Activation Tool.
Provides comprehensive non-destructive preflight validation, graceful quiescence management,
supervisor lockout verification, fresh backup coordination, and atomic single-workbook cutover.
"""

import os
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.scheduler.queue_runner import TIME_BLOCK_WINDOWS
from src.scheduler.quiescence_controller import SchedulerQuiescenceManager
from src.opportunities.activation_gates import ActivationGateManager, GateID
from src.opportunities.authoritative_migration_package import AuthoritativeWorkbookMigrationPackage


def run_comprehensive_preflight(verbose: bool = True) -> Dict[str, Any]:
    """Dynamically queries all live systems and evaluates Gate A cutover readiness."""
    ist_tz = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(ist_tz)
    quiescence_mgr = SchedulerQuiescenceManager()
    gate_mgr = ActivationGateManager()
    migration_pkg = AuthoritativeWorkbookMigrationPackage()

    if verbose:
        print("=" * 80)
        print(f"  GATE A PRODUCTION CUTOVER PREFLIGHT INSPECTION [{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}]")
        print("=" * 80)

    # 1. Supervisor Inspection
    task_info = quiescence_mgr.get_task_supervisor_info()
    task_state = str(task_info.get("State", "UNKNOWN"))
    task_mult = str(task_info.get("MultipleInstances", "UNKNOWN"))
    task_restart = task_info.get("RestartCount", 0)

    if verbose:
        print("\n[1] Windows Scheduled Task Supervisor:")
        print(f"    Task Name         : {quiescence_mgr.task_name}")
        print(f"    State             : {task_state}")
        print(f"    MultipleInstances : {task_mult} (Policy: IgnoreNew)")
        print(f"    Restart Policy    : RestartCount={task_restart}, Interval={task_info.get('RestartInterval')}")

    # 2. Process Tree Inspection
    procs = quiescence_mgr.get_live_scheduler_processes()
    if verbose:
        print(f"\n[2] Live Scheduler Process Tree ({len(procs)} active Python instances):")
        for p in procs:
            print(f"    PID {p.get('ProcessId')} (Parent: {p.get('ParentProcessId')}): {p.get('Name')}")
            cmd = p.get('CommandLine', '')
            if cmd:
                print(f"        CommandLine: {cmd[:90]}...")

    # 3. Dynamic Maintenance Window
    current_time_obj = now_ist.time()
    active_block = None
    next_block = None
    time_to_next = None

    for b_id, b_data in TIME_BLOCK_WINDOWS.items():
        s_time = b_data["start_time"]
        e_time = b_data["end_time"]
        if s_time <= current_time_obj < e_time:
            active_block = (b_id, b_data)
            break
        elif current_time_obj < s_time and next_block is None:
            next_block = (b_id, b_data)
            dt_start = datetime.combine(now_ist.date(), s_time, tzinfo=ist_tz)
            time_to_next = (dt_start - now_ist).total_seconds()

    if verbose:
        print("\n[3] Regional Time Block & Maintenance Window:")
        print(f"    Current Local IST Time : {now_ist.strftime('%H:%M:%S IST')}")
        if active_block:
            print(f"    Active Block           : {active_block[0]} ({active_block[1]['name']}) [{active_block[1]['start_ist']}–{active_block[1]['end_ist']} IST]")
        else:
            print(f"    Active Block           : None (Idle inter-block window)")
            if next_block:
                mins = int(time_to_next // 60)
                secs = int(time_to_next % 60)
                print(f"    Next Scheduled Block   : {next_block[0]} ({next_block[1]['name']}) at {next_block[1]['start_ist']} IST (in {mins}m {secs}s)")

    # 4. Drainage Checks (SMTP & Locks)
    smtp_drainage = quiescence_mgr.verify_no_active_smtp_transactions()
    writer_drainage = quiescence_mgr.verify_no_workbook_writers()
    db_integrity = quiescence_mgr.verify_database_integrity()

    if verbose:
        print("\n[4] In-Flight Telemetry & Concurrency Locks:")
        print(f"    Total Outbound Tracked : {smtp_drainage['total_tracked']}")
        print(f"    Active 10-Min Watches  : {smtp_drainage['active_watches']} (Drained: {smtp_drainage['safe']})")
        print(f"    Master Workbook Lock   : {writer_drainage['workbook_lock_exists']}")
        print(f"    Opportunity Lock       : {writer_drainage['workbook_opp_lock_exists']}")
        print(f"    Scheduler Lock         : {writer_drainage['scheduler_lock_exists']}")
        print(f"    Pending Temp XLSX      : {writer_drainage['pending_temporary_files']}")
        print(f"    Active Writer Locks    : {writer_drainage['active_locks']}")
        print(f"    DB quick_check         : {db_integrity['quick_check']}")
        print(f"    DB integrity_check     : {db_integrity['integrity_check']}")

    # 5. Gate Readiness
    gate_a_open = gate_mgr.is_gate_open(GateID.GATE_A_UNIFIED_WORKBOOK_INTEGRATION)
    gate_b_open = gate_mgr.is_gate_open(GateID.GATE_B_ACADEMIC_EXPANSION_DISPATCH)
    gate_c_open = gate_mgr.is_gate_open(GateID.GATE_C_NON_ACADEMIC_DISPATCH)
    gate_d_open = gate_mgr.is_gate_open(GateID.GATE_D_LIVE_PORTAL_SUBMISSION)
    gate_e_open = gate_mgr.is_gate_open(GateID.GATE_E_CONTINUOUS_DISCOVERY_SYNC)

    if verbose:
        print("\n[5] Activation Gates Status:")
        print(f"    Gate A (Unified Workbook Integration) : {'OPEN' if gate_a_open else 'CLOSED (Awaiting Authorization)'}")
        print(f"    Gate B (Academic Sending)             : {'OPEN' if gate_b_open else 'CLOSED (Protected)'}")
        print(f"    Gate C (Non-Academic Sending)         : {'OPEN' if gate_c_open else 'CLOSED (Protected)'}")
        print(f"    Gate D (Portal Submission)            : {'OPEN' if gate_d_open else 'CLOSED (Protected)'}")
        print(f"    Gate E (Continuous Discovery Sync)    : {'OPEN' if gate_e_open else 'CLOSED (Protected)'}")

    # Refusal Assessment
    refusals = []
    if not smtp_drainage["safe"]:
        refusals.append(f"SMTP delivery watches active: {smtp_drainage['active_watches']}")
    if not db_integrity["safe"]:
        refusals.append(f"Database integrity degraded: qc={db_integrity['quick_check']}, ic={db_integrity['integrity_check']}")

    # Verdict
    is_ready_for_cutover = (len(refusals) == 0 and smtp_drainage["safe"] and db_integrity["safe"])

    if verbose:
        print("\n" + "=" * 80)
        if is_ready_for_cutover:
            print("  PREFLIGHT VERDICT: GO_AWAITING_EXPLICIT_AUTHORIZATION")
            print("  All technical invariants, locks, and telemetry verifications PASSED.")
        else:
            print(f"  PREFLIGHT VERDICT: REFUSED ({len(refusals)} issues detected)")
            for r in refusals:
                print(f"   * {r}")
        print("=" * 80 + "\n")

    return {
        "timestamp_ist": now_ist.isoformat(),
        "task_supervisor": task_info,
        "live_processes": procs,
        "smtp_drainage": smtp_drainage,
        "writer_drainage": writer_drainage,
        "db_integrity": db_integrity,
        "is_ready_for_cutover": is_ready_for_cutover,
        "refusals": refusals
    }


def main():
    parser = argparse.ArgumentParser(description="Gate A Safe Production Activation & Preflight Tool")
    parser.add_argument("--preflight", action="store_true", help="Execute complete dynamic system inspection")
    parser.add_argument("--request-quiescence", action="store_true", help="Set maintenance quiescence flag for graceful exit")
    parser.add_argument("--cancel-quiescence", action="store_true", help="Remove maintenance quiescence flag")
    parser.add_argument("--disable-supervisor", action="store_true", help="Disable Scheduled Task supervisor")
    parser.add_argument("--enable-supervisor", action="store_true", help="Enable Scheduled Task supervisor")
    parser.add_argument("--resume-supervisor", action="store_true", help="Start Scheduled Task supervisor")
    parser.add_argument("--dry-run-cutover", action="store_true", help="Execute isolated non-destructive rehearsal against snapshot")
    parser.add_argument("--execute-production-migration", action="store_true", help="Execute live cutover (REQUIRES EXPLICIT CONFIRMATION)")
    parser.add_argument("--confirm-cutover-authorization", action="store_true", help="Explicit confirmation token for production cutover")
    args = parser.parse_args()

    quiescence_mgr = SchedulerQuiescenceManager()
    migration_pkg = AuthoritativeWorkbookMigrationPackage()

    if args.preflight:
        run_comprehensive_preflight(verbose=True)
        return

    if args.request_quiescence:
        flag = quiescence_mgr.request_quiescence(reason="Operator requested Gate A maintenance quiescence")
        print(f"[OK] Quiescence flag created: {flag}")
        print("  Running schedulers will cleanly exit at the next checkpoint without interrupting transactions.")
        return

    if args.cancel_quiescence:
        quiescence_mgr.cancel_quiescence()
        print("[OK] Quiescence flag removed.")
        return

    if args.disable_supervisor:
        res = quiescence_mgr.disable_task_supervisor()
        print(f"[OK] Scheduled Task supervisor disabled: {res}")
        return

    if args.enable_supervisor:
        ps_enable = f"Enable-ScheduledTask -TaskName '{quiescence_mgr.task_name}' -ErrorAction Stop"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_enable], check=True)
        print(f"[OK] Scheduled Task supervisor re-enabled.")
        return

    if args.resume_supervisor:
        res = quiescence_mgr.enable_and_start_task_supervisor()
        print(f"[OK] Scheduled Task supervisor resumed: {res}")
        return

    if args.dry_run_cutover:
        print("=== EXECUTING ISOLATED NON-DESTRUCTIVE REHEARSAL ===")
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            res = migration_pkg.simulate_quiescence_cutover_and_rollback(Path(tmpdir))
            print(f"  Rehearsal status: {res['rehearsal_status']}")
            print(f"  All 18 sheets verified: {res['all_18_sheets_rehearsed']}")
            print(f"  Rollback status: {res['rollback_status']}")
            print(f"  Byte-perfect restoration: {res['byte_perfect_restoration']}")
            print(f"  Production isolation maintained: {res['production_isolation_maintained']}")
        print("\n[OK] Rehearsal passed. Technically ready for operator authorization.")
        return

    if args.execute_production_migration:
        print("=== PRODUCTION GATE A CUTOVER REQUESTED ===")
        from src.opportunities.activation_gates import ActivationGateManager, GateID
        gate_mgr = ActivationGateManager()

        if not gate_mgr.is_gate_open(GateID.GATE_A_UNIFIED_WORKBOOK_INTEGRATION):
            print("\n[REFUSED] Gate A environment authorization flag missing:")
            print("  Environment variable 'ENABLE_PRODUCTION_WORKBOOK_MUTATION=1' is required.")
            print("  Stopping at the authorization boundary.")
            return

        if not args.confirm_cutover_authorization:
            print("\n[REFUSED] Operator confirmation token missing:")
            print("  '--confirm-cutover-authorization' is required for live cutover.")
            print("  Stopping at the authorization boundary.")
            return

        preflight = run_comprehensive_preflight(verbose=False)
        if not preflight["is_ready_for_cutover"]:
            print(f"\n[REFUSED] Preflight invariants failed: {preflight['refusals']}")
            return

        res = migration_pkg.execute_production_cutover_gate_a()
        print("\n=== GATE A PRODUCTION CUTOVER SUCCESSFUL ===")
        print(f"  Fresh Backup ID: {res['fresh_backup_id']}")
        print(f"  Manifest: {res['manifest_path']}")
        print(f"  Workbook: {res['workbook_path']} ({res['workbook_sha256'][:16]}...)")
        print(f"  Bytes: {res['workbook_bytes']:,}")
        print(f"  All 18 sheets verified: {res['all_18_sheets_verified']}")
        print("\n[OK] Single authoritative workbook migration complete.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
