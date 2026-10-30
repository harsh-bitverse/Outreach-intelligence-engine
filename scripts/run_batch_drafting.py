"""
Scalable AI Batch Drafter & Queue Auditor CLI Tool.
Provides visibility into progress metrics, durable cursor state,
candidate fetching, batch importing, and institutional sending limits.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings
from src.database.connection import get_db_connection
from src.database.repository import OutreachRepository
from src.intelligence.batch_drafter import batch_ai_drafter


def show_status(repo: OutreachRepository):
    metrics = batch_ai_drafter.get_progress_metrics(repo)
    print("\n" + "=" * 60)
    print("      OUTREACH INTELLIGENCE ENGINE — BATCH DRAFTING STATUS")
    print("=" * 60)
    print(f"Total Workbook Prospects   : {metrics['total_workbook_prospects']}")
    print(f"Verified Candidates (Email): {metrics['verified_candidates']}")
    print(f"Suppressed / Replied       : {metrics['suppressed_count']}")
    print("-" * 60)
    print(f"AI Drafts Completed        : {metrics['ai_drafts_completed']}")
    print(f"Queued Pending Dispatch    : {metrics['queued_pending']}")
    print(f"Dispatched / Simulated Sent: {metrics['dispatched_count']}")
    print(f"Needs Manual Research      : {metrics['needs_research_count']}")
    print(f"Failed Validation          : {metrics['failed_validation_count']}")
    print(f"Remaining to Draft         : {metrics['remaining_to_draft']}")
    print("-" * 60)
    cs = metrics["cursor_state"]
    print("Durable Cursor State:")
    print(f"  Last Time Block          : {cs.get('last_time_block')}")
    print(f"  Last Sequence Order      : {cs.get('last_sequence_order')}")
    print(f"  Last Professor ID        : {cs.get('last_professor_id')}")
    print(f"  Total Drafted Under Cursor: {cs.get('total_drafted')}")
    print("=" * 60 + "\n")


def show_next_candidates(repo: OutreachRepository, n: int = 5, time_block: str = None):
    candidates = batch_ai_drafter.get_next_candidates(repo, batch_size=n, time_block=time_block)
    print(f"\nNext {len(candidates)} Candidate(s) in Workbook Order:")
    print("-" * 80)
    for idx, c in enumerate(candidates, 1):
        name = c.get('full_name') or c.get('name', 'N/A')
        uni = c.get('university_name') or c.get('university', 'N/A')
        contrib = c.get('potential_contribution') or c.get('computational_contribution', 'N/A')
        print(f"{idx}. [{c.get('time_block', 'N/A')}] #{c.get('time_block_order', 'N/A')} - {name} <{c.get('email')}>")
        print(f"   University: {uni} (Rank: {c.get('qs_rank')})")
        print(f"   Research  : {c.get('research_areas')}")
        print(f"   Overlap   : {c.get('research_overlap')}")
        print(f"   Contrib   : {contrib}")
        print(f"   URL       : {c.get('faculty_profile_url') or c.get('evidence_url')}")
        print("-" * 80)


def import_batch_file(repo: OutreachRepository, filepath: str):
    path = Path(filepath)
    if not path.exists():
        print(f"Error: file not found: {filepath}")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        drafts = json.load(f)

    if not isinstance(drafts, list):
        print("Error: JSON must contain a list of draft objects.")
        sys.exit(1)

    print(f"\nImporting batch of {len(drafts)} AI-authored draft(s) from {path.name}...")
    success_count = 0
    failure_count = 0

    for idx, d in enumerate(drafts, 1):
        prof_name = d.get("professor_name", "Unknown")
        email = d.get("recipient_email", "Unknown")
        res = batch_ai_drafter.stage_ai_draft(repo, d)
        if res.get("success"):
            success_count += 1
            print(f"  [{idx}/{len(drafts)}] [OK] {prof_name} ({email}) -> Draft ID: {res['result']['draft_id']}")
        else:
            failure_count += 1
            print(f"  [{idx}/{len(drafts)}] [FAILED] {prof_name} ({email}) -> Error: {res.get('error')}")

    print("\nBatch Import Complete:")
    print(f"  Successfully Staged: {success_count}")
    print(f"  Failed Validation  : {failure_count}")
    show_status(repo)


def main():
    parser = argparse.ArgumentParser(description="Outreach Engine AI Batch Drafting CLI")
    parser.add_argument("--status", action="store_true", help="Show current drafting and queue metrics")
    parser.add_argument("--next", type=int, default=0, help="Show next N candidates for drafting")
    parser.add_argument("--block", type=str, default=None, help="Filter next candidates by time block")
    parser.add_argument("--import-file", type=str, default=None, help="Import AI drafts from JSON file")

    args = parser.parse_args()
    conn = get_db_connection()
    repo = OutreachRepository(conn)

    if args.import_file:
        import_batch_file(repo, args.import_file)
    elif args.next > 0:
        show_next_candidates(repo, n=args.next, time_block=args.block)
    else:
        show_status(repo)


if __name__ == "__main__":
    main()
