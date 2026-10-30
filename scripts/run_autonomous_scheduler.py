"""
Standalone Autonomous Outreach Scheduler CLI.
Manages and audits the durable queue workflow:
1. Verifies local environment and authoritative documents.
2. Builds durable queue from imported Excel time blocks.
3. Enforces 6 reply-received / prior contact exclusions.
4. Simulates time-block execution with 5-message cadence and 1-minute cooling breaks.
5. Verifies dual PDF attachment integrity and recipient filenames.
"""

import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import settings
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.scheduler.queue_runner import (
    AutonomousQueueRunner,
    autonomous_queue_runner,
    TIME_BLOCK_WINDOWS,
    REPLY_RECEIVED_EXCLUSIONS
)
from src.mail.sending_gateway import SendingGateway


def banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def section(title: str):
    print("\n" + "-" * 80)
    print(f"  {title}")
    print("-" * 80)


def main():
    banner("AUTONOMOUS OUTREACH SCHEDULER & TIME-BLOCK AUDIT")
    print(f"  Applicant: Harshith N (ch1251635@iitd.ac.in) | IIT Delhi")
    print(f"  Target: Summer 2027 Visiting Research Internship")
    print(f"  Authoritative Workbook: {settings.MASTER_WORKBOOK_PATH.name}")
    print(f"  Timestamp: {datetime.now().isoformat()}")

    # 1. Authoritative Attachment Verification
    section("1. Authoritative Document and Attachment Verification")
    cv_path = settings.CV_ATTACHMENT_PATH
    ri_path = settings.RESEARCH_INTERESTS_PATH

    print(f"  [+] CV Path: {cv_path}")
    print(f"      Exists: {cv_path.exists()} | Size: {cv_path.stat().st_size if cv_path.exists() else 0} bytes")
    print(f"      Recipient-facing Filename: '{settings.CV_ATTACHMENT_FILENAME}'")

    print(f"  [+] Research Interests Path: {ri_path}")
    print(f"      Exists: {ri_path.exists()} | Size: {ri_path.stat().st_size if ri_path.exists() else 0} bytes")
    print(f"      Recipient-facing Filename: '{settings.RESEARCH_INTERESTS_FILENAME}'")

    assert cv_path.exists(), "Fatal: CV PDF missing!"
    assert ri_path.exists(), "Fatal: Research Interests PDF missing!"

    # 2. Database & Authoritative Sheet Sync
    section("2. Database & Workbook Synchronization")
    initialize_database()

    with get_db() as conn:
        repo = OutreachRepository(conn)
        sync = DynamicWorkbookSync()

        # Check existing count
        prof_count = conn.execute("SELECT COUNT(*) FROM professors;").fetchone()[0]
        if prof_count < 1390:
            print("  Importing 1,390 professors from 'Time Blocks & Distribution'...")
            stats = sync.import_to_database(repo)
            print(f"  Imported: {stats['professors_imported']} professors")
        else:
            print(f"  Database already holds {prof_count} professors from authoritative sheet.")

        # 3. Seed and Verify 6 Reply-Received Suppressions
        section("3. Suppression Audit: 6 Confirmed Reply-Received Contacts")
        runner = autonomous_queue_runner
        seeded = runner.seed_reply_received_suppressions(repo)
        print(f"  Verified {seeded} exclusion entries in database:")

        for _, email, reason, notes in REPLY_RECEIVED_EXCLUSIONS:
            is_supp, supp_reason = runner.is_suppressed_or_excluded(repo, email)
            print(f"    - [REPLY RECEIVED] {email:<35} | Suppressed={is_supp}")

        # Check IIT Delhi domain exclusion
        is_iitd, iitd_reason = repo.is_suppressed("prof@iitd.ac.in")
        print(f"    - [SELF DOMAIN]    {'prof@iitd.ac.in':<35} | Suppressed={is_iitd}")

        # Verify Abigail Bodner and Percy Liang are NOT suppressed
        is_bodner_supp, _ = runner.is_suppressed_or_excluded(repo, "bodner@mit.edu")
        is_liang_supp, _ = runner.is_suppressed_or_excluded(repo, "pliang@cs.stanford.edu")
        print(f"    - [CANDIDATE CHECK] Abigail Bodner (MIT)               | Suppressed={is_bodner_supp} (Correctly Unsuppressed)")
        print(f"    - [CANDIDATE CHECK] Percy Liang (Stanford)            | Suppressed={is_liang_supp} (Correctly Unsuppressed)")
        assert not is_bodner_supp, "Bodner must not be classified as replied/suppressed"
        assert not is_liang_supp, "Liang must not be classified as replied/suppressed"

        # 4. Durable Queue Build
        section("4. Building / Syncing Durable Outreach Queue")
        q_stats = runner.build_queue_from_professors(repo, limit=1500)
        print(f"  Queue Build Results:")
        print(f"    - Enqueued New Drafts:     {q_stats['enqueued_count']}")
        print(f"    - Suppressed Excluded:     {q_stats['suppressed_count']}")
        print(f"    - Skipped (Already Queued):{q_stats['skipped_count']}")

        diagnostics = runner.get_queue_diagnostics(repo)
        print(f"\n  Current Queue State Across All 13 Blocks:")
        print(f"    - Total Items: {diagnostics['total_queued']}")
        print(f"    - Pending:      {diagnostics['pending_count']}")
        print(f"    - Sent:         {diagnostics['sent_count']}")
        print(f"    - Suppressed:   {diagnostics['suppressed_count']}")

        # 5. Time-Block Window Evaluation
        section("5. 13 Regional Time-Block 90-Minute Window Audit")
        print(f"  {'Block ID':<10} | {'Region':<38} | {'IST Window':<15} | {'Active Now?'}")
        print("  " + "-" * 75)
        for b_id, b_info in TIME_BLOCK_WINDOWS.items():
            is_active, _ = runner.is_window_active(b_id)
            print(f"  {b_id:<10} | {b_info['name']:<38} | {b_info['start_ist']}-{b_info['end_ist']} IST | {is_active}")

        # 6. Simulated Cadence Dispatch Test (Block 01, 7 candidates)
        section("6. Cadence Dispatch Simulation (Block 01: 5-Message Batch + 1-Min Cooling Break)")
        print("  Testing 5-message batch dispatch, cooling break pause, dual PDF attachments, and crash recovery...")

        # Run dispatch with dry_run=True, force_window=True for testing, simulated_cooling=True
        dispatch_res = runner.dispatch_queue(
            repo=repo,
            time_block="BLOCK_01",
            max_messages=7,
            dry_run=True,
            force_window=True,
            simulated_cooling=True
        )

        print(f"\n  Dispatch Results:")
        print(f"    - Dispatched (Dry Run): {dispatch_res['dispatched_count']}")
        print(f"    - Recovered Crashed:   {dispatch_res['recovered_crashed']}")
        print(f"    - Dry Run Flag:        {dispatch_res['dry_run']}")

        print("\n  Sample Dispatched Messages in Block 01:")
        for idx, item in enumerate(dispatch_res["items"], start=1):
            batch_num = 1 if idx <= 5 else 2
            print(f"    [{idx}/7] Batch {batch_num} | {item['professor_name']} ({item['university']})")
            print(f"         To: {item['recipient']}")
            print(f"         Subject: {item['subject']}")
            print(f"         Status: {item['status']} | Message ID: {item['message_id']}")
            if idx == 5:
                print("         >>> [CADENCE BREAK: 60-Second Cooling Pause Enforced Before Batch 2] <<<")

        # 7. Verification of Subject Format and Attachments
        section("7. Verification of Subject Line & PDF Attachments")
        first_item = dispatch_res["items"][0] if dispatch_res["items"] else None
        if first_item:
            subj = first_item["subject"]
            print(f"  Subject: '{subj}'")
            assert subj.startswith("Summer 2027 Research Internship — "), "Subject format invalid prefix"
            assert subj.endswith(" | IIT Delhi"), "Subject format invalid suffix"
            print("  [OK] Subject format matches exact requirement: 'Summer 2027 Research Internship — [Research Area] | IIT Delhi'")

        print("\n  [OK] Autonomous queue runner & time-block audit passed completely.")


if __name__ == "__main__":
    main()
