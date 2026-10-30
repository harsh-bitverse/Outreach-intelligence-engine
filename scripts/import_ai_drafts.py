"""
CLI Script to validate and import genuinely AI-authored drafts into SQLite.
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository
from src.intelligence.ai_draft_importer import ai_draft_importer


def main():
    parser = argparse.ArgumentParser(description="Import AI-authored drafts into SQLite queue.")
    parser.add_argument("--file", type=str, required=True, help="Path to JSON file containing AI-authored drafts")
    args = parser.parse_args()

    json_path = Path(args.file)
    if not json_path.exists():
        print(f"Error: File not found: {json_path}")
        sys.exit(1)

    initialize_database()
    with get_db() as conn:
        repo = OutreachRepository(conn)
        print(f"Importing AI drafts from {json_path}...")
        res = ai_draft_importer.import_from_json_file(json_path, repo)
        print(f"Import completed:")
        print(f"  Imported & Staged: {res['imported_count']}")
        print(f"  Rejected:          {res['rejected_count']}")
        for r in res["results"]:
            print(f"  - [{r['status']}] {r['recipient_email']}: {r.get('subject', '')} (words: {r.get('word_count', 0)})")
            if r["status"] == "REJECTED":
                print(f"      Reason: {r['error']}")


if __name__ == "__main__":
    main()
