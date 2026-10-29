"""
Authoritative Master Workbook Import and Reconciliation Script.
Imports professors and universities from the authoritative Excel workbook into SQLite.
"""

import sys
from pathlib import Path

# Ensure root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import settings
from src.database.connection import initialize_database, get_db
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync


def main():
    print(f"Targeting Master Workbook: {settings.MASTER_WORKBOOK_PATH}")
    if not settings.MASTER_WORKBOOK_PATH.exists():
        print(f"Error: Master workbook does not exist at {settings.MASTER_WORKBOOK_PATH}")
        sys.exit(1)

    initialize_database()
    with get_db() as conn:
        repo = OutreachRepository(conn)
        sync = DynamicWorkbookSync()
        print("Starting dynamic schema import...")
        stats = sync.import_to_database(repo)
        print("Import completed successfully!")
        print(f"  Professors Imported: {stats['professors_imported']}")
        print(f"  Universities Imported: {stats['universities_imported']}")
        print(f"  Suppressed Contacts: {stats['suppressed_detected']}")
        print(f"  Sheets Found: {stats['sheets_found']}")


if __name__ == "__main__":
    main()
