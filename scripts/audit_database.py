"""
Forensic Audit of Authoritative Excel and SQLite Database Records.
Analyzes row counts, verified emails, blank emails, suppressions, and discrepancies.
"""

import openpyxl
import sqlite3
import re
import sys
from pathlib import Path
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import settings


def run_audit():
    print("=" * 80)
    print("FORENSIC AUDIT: EXCEL MASTER WORKBOOK VS SQLITE STATE")
    print("=" * 80)

    # 1. Audit Excel
    wb_path = settings.MASTER_WORKBOOK_PATH
    print(f"\n1. Excel Master Workbook: {wb_path}")
    wb = openpyxl.load_workbook(wb_path, data_only=True)
    ws_prof = wb["Professor Database"]

    headers = [cell.value for cell in ws_prof[1]]
    excel_rows = list(ws_prof.iter_rows(min_row=2, values_only=True))
    total_excel_rows = len(excel_rows)
    print(f"  Total Data Rows in Sheet: {total_excel_rows}")

    excel_with_email = []
    excel_blank_email = []
    excel_verified_email = []
    excel_unverified_email = []
    excel_suppressed_candidates = []

    for r_idx, r in enumerate(excel_rows, start=2):
        name = str(r[4] or "").strip()
        uni = str(r[2] or "").strip()
        email = str(r[7] or "").strip()
        status = str(r[8] or "").strip()

        if not email:
            excel_blank_email.append((r_idx, name, uni))
        else:
            excel_with_email.append((r_idx, name, uni, email, status))
            if status == "VERIFIED":
                excel_verified_email.append((r_idx, name, uni, email))
            else:
                excel_unverified_email.append((r_idx, name, uni, email))

        if any(k in name.lower() for k in ["wong", "wu", "chenshu", "kai-kit"]):
            excel_suppressed_candidates.append((r_idx, name, uni, email))

    print(f"  Rows with non-empty Email: {len(excel_with_email)}")
    print(f"  Rows with blank/missing Email: {len(excel_blank_email)}")
    print(f"  Verified Emails in Excel: {len(excel_verified_email)}")
    print(f"  Unverified/Pending Emails in Excel: {len(excel_unverified_email)}")
    print(f"\n  Wong / Wu records in Excel ({len(excel_suppressed_candidates)}):")
    for rec in excel_suppressed_candidates:
        print(f"    - Row {rec[0]}: {rec[1]} ({rec[2]}) -> Email: {rec[3]}")

    # 2. Audit SQLite
    print(f"\n2. SQLite Database: {settings.SQLITE_DB_PATH}")
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row

    sqlite_total = conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0]
    sqlite_blank = conn.execute("SELECT COUNT(*) FROM professors WHERE email IS NULL OR email = ''").fetchone()[0]
    sqlite_ver = conn.execute("SELECT COUNT(*) FROM professors WHERE email_verification_status = 'VERIFIED' AND email IS NOT NULL AND email != ''").fetchone()[0]
    sqlite_unver = conn.execute("SELECT COUNT(*) FROM professors WHERE email_verification_status != 'VERIFIED' AND email IS NOT NULL AND email != ''").fetchone()[0]
    sqlite_supp = conn.execute("SELECT * FROM professors WHERE outreach_status = 'SUPPRESSED'").fetchall()

    print(f"  Total Professors in SQLite: {sqlite_total}")
    print(f"  Professors with blank Email: {sqlite_blank}")
    print(f"  Verified Email Count: {sqlite_ver}")
    print(f"  Unverified Email Count: {sqlite_unver}")
    print(f"  Suppressed Professors in SQLite ({len(sqlite_supp)}):")
    for sp in sqlite_supp:
        print(f"    - {sp['professor_id']} | {sp['full_name']} ({sp['university_name']}) | {sp['email']} | Status: {sp['outreach_status']} | Notes: {sp['notes']}")

    # 3. Suppression List
    supp_list = conn.execute("SELECT * FROM suppression_list").fetchall()
    print(f"\n3. Permanent Suppression Table Records ({len(supp_list)}):")
    for s in supp_list:
        target = s["email"] or s["domain"] or "ALL"
        print(f"    - [{s['reason']}] {target}: {s['notes']}")

    # 4. University Coverage
    uni_count = conn.execute("SELECT COUNT(DISTINCT university_name) FROM professors").fetchone()[0]
    uni_db_count = conn.execute("SELECT COUNT(*) FROM universities").fetchone()[0]
    print(f"\n4. Universities in SQLite: {uni_count} distinct in professors, {uni_db_count} in universities table")

    conn.close()


if __name__ == "__main__":
    run_audit()
