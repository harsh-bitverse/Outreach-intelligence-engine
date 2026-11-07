import sqlite3
import logging
from pathlib import Path
from src.opportunities.schema import apply_opportunity_migrations

db_path = Path("data/outreach_engine.db")
dev_db_path = Path("data/development/opportunity_dev.db")

print(f"Applying unified opportunity migrations to authoritative database: {db_path}...")
conn = sqlite3.connect(str(db_path))

# 1. Apply opportunity tables
res = apply_opportunity_migrations(conn)
print(f"Schema status: {res['status']}")

# 2. Populate organizations and opportunities from dev DB
cur = conn.cursor()
conn.execute(f"ATTACH DATABASE '{dev_db_path.as_posix()}' AS dev_db;")
conn.execute("INSERT OR REPLACE INTO organizations SELECT * FROM dev_db.organizations;")
conn.execute("INSERT OR REPLACE INTO opportunities SELECT * FROM dev_db.opportunities;")
conn.commit()

cur.execute("SELECT COUNT(*) FROM organizations;")
org_count = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM opportunities;")
opp_count = cur.fetchone()[0]

cur.execute("SELECT opportunity_type, COUNT(*) FROM opportunities GROUP BY opportunity_type;")
breakdown = dict(cur.fetchall())

# Integrity check
cur.execute("PRAGMA integrity_check;")
ic = cur.fetchone()[0]
cur.execute("PRAGMA quick_check;")
qc = cur.fetchone()[0]

conn.close()

print(f"\n[SUCCESS] Authoritative SQLite Database Unified:")
print(f"  Organizations: {org_count}")
print(f"  Opportunities: {opp_count}")
print(f"  Breakdown: {breakdown}")
print(f"  quick_check: {qc}")
print(f"  integrity_check: {ic}")
