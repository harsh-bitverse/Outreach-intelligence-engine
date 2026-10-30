"""
Database & Queue Transactional Repair Script (Session 06).
Distinguishes candidates awaiting drafting from genuinely approved AI drafts.
Archives legacy template simulations, resets queue to contain strictly AI drafts,
and aligns the durable sequential generation cursor.
"""

import json
import sqlite3
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings


def repair_database():
    db_path = settings.SQLITE_DB_PATH
    print(f"Connecting to database: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()
        conn.execute("BEGIN TRANSACTION;")

        # 1. Create simulation_audit_log to preserve all dry-run historical evidence
        cur.execute("""
            CREATE TABLE IF NOT EXISTS simulation_audit_log (
                simulation_id TEXT PRIMARY KEY,
                queue_id TEXT NOT NULL,
                professor_id TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                time_block_id TEXT,
                sequence_order INTEGER,
                dispatched_at TEXT NOT NULL,
                draft_type TEXT NOT NULL,
                notes TEXT
            );
        """)

        # 2. Archive legacy SIMULATED_SENT template records into simulation_audit_log
        legacy_sims = cur.execute("""
            SELECT q.queue_id, q.professor_id, q.draft_id, p.email as recipient_email,
                   q.time_block_id, q.sequence_order, q.dispatched_at
            FROM outreach_queue q
            JOIN professors p ON q.professor_id = p.professor_id
            WHERE q.status = 'SIMULATED_SENT' AND q.draft_id LIKE 'draft_%';
        """).fetchall()

        for idx, sim in enumerate(legacy_sims, 1):
            sim_id = f"sim_legacy_{idx:04d}_{sim['queue_id']}"
            cur.execute("""
                INSERT OR REPLACE INTO simulation_audit_log (
                    simulation_id, queue_id, professor_id, draft_id, recipient_email,
                    time_block_id, sequence_order, dispatched_at, draft_type, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), 'LEGACY_TEMPLATE', 'Dry-run template simulation from Session 01-04 audit');
            """, (
                sim_id, sim["queue_id"], sim["professor_id"], sim["draft_id"],
                sim["recipient_email"], sim["time_block_id"], sim["sequence_order"],
                sim["dispatched_at"]
            ))

        print(f"[1/6] Archived {len(legacy_sims)} legacy template simulations into simulation_audit_log.")

        # 3. Mark all legacy template drafts in outreach_drafts as DEPRECATED_TEMPLATE
        updated_templates = cur.execute("""
            UPDATE outreach_drafts
            SET status = 'DEPRECATED_TEMPLATE'
            WHERE draft_id LIKE 'draft_%';
        """).rowcount
        print(f"[2/6] Deprecated {updated_templates} legacy template drafts in outreach_drafts.")

        # 4. Remove unwritten candidates from outreach_queue
        # outreach_queue must strictly contain genuinely drafted and staged emails
        deleted_queue_items = cur.execute("""
            DELETE FROM outreach_queue
            WHERE draft_id LIKE 'draft_%';
        """).rowcount
        print(f"[3/6] Purged {deleted_queue_items} unwritten template items from outreach_queue.")

        # 4. Ensure all 10 AI drafts in outreach_drafts have status APPROVED
        cur.execute("""
            UPDATE outreach_drafts
            SET status = 'APPROVED'
            WHERE draft_id LIKE 'aidraft_%';
        """)

        # 5. Ensure all 10 genuine AI drafts are correctly represented in outreach_queue
        ai_drafts = cur.execute("""
            SELECT d.draft_id, d.professor_id, d.recipient_email, d.subject,
                   p.time_block, p.time_block_order
            FROM outreach_drafts d
            JOIN professors p ON d.professor_id = p.professor_id
            WHERE d.draft_id LIKE 'aidraft_%';
        """).fetchall()

        for d in ai_drafts:
            queue_id = f"q_ai_{d['time_block_order']:04d}_{d['professor_id'][5:15]}"
            cur.execute("""
                INSERT INTO outreach_queue (
                    queue_id, professor_id, draft_id, time_block_id,
                    window_start_ist, window_end_ist, sequence_order,
                    status, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '08:00', '09:30', ?, 'PENDING', 0, datetime('now'), datetime('now'))
                ON CONFLICT(queue_id) DO UPDATE SET
                    professor_id = excluded.professor_id,
                    draft_id = excluded.draft_id,
                    time_block_id = excluded.time_block_id,
                    sequence_order = excluded.sequence_order,
                    status = 'PENDING',
                    attempts = 0,
                    dispatched_at = NULL,
                    updated_at = datetime('now');
            """, (queue_id, d["professor_id"], d["draft_id"], d["time_block"], d["time_block_order"]))

        print(f"[4/6] Reconciled and staged {len(ai_drafts)} genuine AI drafts in outreach_queue with status PENDING.")

        # 6. Reconcile professors table outreach_status
        # First reset all professors to AWAITING_DRAFT
        cur.execute("""
            UPDATE professors
            SET outreach_status = 'AWAITING_DRAFT', updated_at = datetime('now');
        """)

        # Set all genuine drafted professors to DRAFT_READY
        cur.execute("""
            UPDATE professors
            SET outreach_status = 'DRAFT_READY', updated_at = datetime('now')
            WHERE professor_id IN (SELECT professor_id FROM outreach_drafts WHERE draft_id LIKE 'aidraft_%' AND status = 'APPROVED');
        """)

        # Set suppressed professors to SUPPRESSED
        cur.execute("""
            UPDATE professors
            SET outreach_status = 'SUPPRESSED', updated_at = datetime('now')
            WHERE email IN (SELECT email FROM suppression_list WHERE email IS NOT NULL)
               OR professor_id IN (
                   SELECT p.professor_id FROM professors p
                   JOIN suppression_list s ON s.domain IS NOT NULL AND lower(p.email) LIKE '%@' || lower(s.domain)
               );
        """)

        # Set all remaining eligible unwritten professors to AWAITING_DRAFT
        cur.execute("""
            UPDATE professors
            SET outreach_status = 'AWAITING_DRAFT', updated_at = datetime('now')
            WHERE outreach_status NOT IN ('DRAFT_READY', 'SUPPRESSED');
        """)

        prof_counts = cur.execute("""
            SELECT outreach_status, count(*) FROM professors GROUP BY outreach_status;
        """).fetchall()
        print(f"[5/6] Updated professors outreach_status:")
        for pc in prof_counts:
            print(f"      - {pc[0]}: {pc[1]}")

        # 7. Reset and align durable sequential cursor
        # The next candidate in workbook order is Order 2 (Jiamou Liu, Auckland)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_drafting_cursor (
                cursor_id TEXT PRIMARY KEY,
                last_sequence_order INTEGER DEFAULT 0,
                last_professor_id TEXT,
                last_time_block TEXT,
                total_drafted INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                total_skipped INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            );
        """)

        # Order 1 (Brent Young) is completed (aidraft_0209dcbdc7)
        # Therefore, the high-water sequential order completed from the start is Order 1!
        cur.execute("""
            INSERT OR REPLACE INTO ai_drafting_cursor (
                cursor_id, last_sequence_order, last_professor_id, last_time_block,
                total_drafted, total_failed, total_skipped, updated_at
            ) VALUES ('WORKBOOK_SEQUENTIAL', 1, 'prof_0209dcbdc769304f', 'BLOCK_01', 10, 0, 0, datetime('now'));
        """)

        state_file = settings.DATA_DIR / "drafts" / "drafting_state.json"
        cursor_data = {
            "cursor_id": "WORKBOOK_SEQUENTIAL",
            "last_sequence_order": 1,
            "last_professor_id": "prof_0209dcbdc769304f",
            "last_time_block": "BLOCK_01",
            "total_drafted": 10,
            "total_failed": 0,
            "total_skipped": 0,
            "updated_at": "2026-10-30T18:55:00Z"
        }
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(cursor_data, f, indent=2)

        print(f"[6/6] Reset production sequential cursor to Order 1 (last continuous completed: Brent Young, next: Order 2).")

        conn.commit()
        print("\nSUCCESS: Database and queue records successfully repaired transactionally.")

    except Exception as e:
        conn.rollback()
        print(f"ERROR: Transaction failed and rolled back: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    repair_database()
