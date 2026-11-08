import openpyxl
import os
import shutil
import sqlite3
from pathlib import Path

wb_path = Path("data/private/Summer_2027_Professor_Database_WITH_TIME_BLOCKS_EXPORT.xlsx")
db_path = Path("data/outreach_engine.db")

print("Loading SQLite scheduled opportunities...")
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute("SELECT opportunity_id, recipient_email FROM unified_outreach_queue WHERE queue_status = 'SCHEDULED_FOR_DISPATCH';")
db_scheduled_items = cur.fetchall()
conn.close()

db_scheduled_emails = {str(r[1]).strip().lower() for r in db_scheduled_items if r[1]}
print(f"Total distinct scheduled emails in SQLite: {len(db_scheduled_emails)}")

print(f"Loading workbook: {wb_path}...")
wb = openpyxl.load_workbook(str(wb_path))
ws = wb["Unified Outreach Queue"]

header = [c.value for c in ws[1]]
idx_qid = header.index("Queue ID") + 1
idx_org = header.index("Organization") + 1
idx_name = header.index("Contact Name") + 1
idx_email = header.index("Contact Email") + 1
idx_status = header.index("Outreach Status") + 1
idx_next = header.index("Next Action") + 1

seen_emails = set()
scheduled_count = 0
suppressed_count = 0

for row_idx in range(2, ws.max_row + 1):
    email_val = str(ws.cell(row=row_idx, column=idx_email).value or "").strip().lower()
    if email_val in db_scheduled_emails:
        if email_val in seen_emails:
            ws.cell(row=row_idx, column=idx_status).value = "SUPPRESSED_DUPLICATE"
            ws.cell(row=row_idx, column=idx_next).value = "SUPPRESSED_SAME_RECIPIENT_COLLISION"
            suppressed_count += 1
        else:
            seen_emails.add(email_val)
            ws.cell(row=row_idx, column=idx_status).value = "SCHEDULED_FOR_DISPATCH"
            ws.cell(row=row_idx, column=idx_next).value = "DISPATCH_IN_ASSIGNED_TIME_BLOCK"
            scheduled_count += 1

print(f"Workbook rows set to SCHEDULED_FOR_DISPATCH: {scheduled_count}")
print(f"Workbook rows set to SUPPRESSED_DUPLICATE: {suppressed_count}")

# Save atomically
temp_wb = wb_path.with_suffix(f".tmp_rec_{os.getpid()}.xlsx")
wb.save(str(temp_wb))
wb.close()
shutil.move(str(temp_wb), str(wb_path))

print(f"[SUCCESS] Workbook saved atomically. Exactly {scheduled_count} scheduled rows and {suppressed_count} duplicates.")
