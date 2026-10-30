"""
Scalable AI Batch Drafter & Persistent Generation Cursor Module.
Maintains a durable generation cursor, manages progressive batch generation,
tracks drafting states (DRAFT_READY, NEEDS_RESEARCH, FAILED_VALIDATION, ALREADY_QUEUED),
and interfaces with AIDraftImporter for SQLite staging.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from config.settings import settings
from src.database.repository import OutreachRepository
from src.intelligence.ai_draft_importer import AIDraftImporter, ai_draft_importer
from src.scheduler.queue_runner import (
    TIME_BLOCK_WINDOWS,
    autonomous_queue_runner,
    REPLY_RECEIVED_EXCLUSIONS
)
from src.security.audit_logger import audit_logger


STATE_FILE_PATH = settings.DATA_DIR / "drafts" / "drafting_state.json"


class BatchAIDrafter:
    """
    Manages sequential, agent-operable batch AI drafting workflows.
    Ensures resumability, progress tracking, and zero duplicate generation.
    """

    CURSOR_ID = "WORKBOOK_SEQUENTIAL"

    def __init__(self, importer: Optional[AIDraftImporter] = None):
        self.importer = importer or ai_draft_importer

    def init_cursor_table(self, repo: OutreachRepository) -> None:
        """Ensure the cursor table exists in the database."""
        repo.conn.execute("""
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
        repo.conn.commit()

    def get_cursor(self, repo: OutreachRepository) -> Dict[str, Any]:
        """Load durable cursor from SQLite, falling back to JSON state file."""
        self.init_cursor_table(repo)
        cur = repo.conn.cursor()
        cur.execute("SELECT * FROM ai_drafting_cursor WHERE cursor_id = ?;", (self.CURSOR_ID,))
        row = cur.fetchone()

        if row:
            return {
                "cursor_id": row["cursor_id"],
                "last_sequence_order": row["last_sequence_order"],
                "last_professor_id": row["last_professor_id"],
                "last_time_block": row["last_time_block"],
                "total_drafted": row["total_drafted"],
                "total_failed": row["total_failed"],
                "total_skipped": row["total_skipped"],
                "updated_at": row["updated_at"]
            }

        # Check JSON state file
        if STATE_FILE_PATH.exists():
            try:
                with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    return state
            except Exception:
                pass

        # Default initial cursor
        return {
            "cursor_id": self.CURSOR_ID,
            "last_sequence_order": 0,
            "last_professor_id": None,
            "last_time_block": "BLOCK_01",
            "total_drafted": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

    def save_cursor(self, repo: OutreachRepository, cursor: Dict[str, Any]) -> None:
        """Persist cursor to SQLite and write transparent JSON state."""
        self.init_cursor_table(repo)
        now_str = datetime.now(timezone.utc).isoformat()
        cursor["updated_at"] = now_str

        repo.conn.execute("""
            INSERT INTO ai_drafting_cursor (
                cursor_id, last_sequence_order, last_professor_id, last_time_block,
                total_drafted, total_failed, total_skipped, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cursor_id) DO UPDATE SET
                last_sequence_order = excluded.last_sequence_order,
                last_professor_id = excluded.last_professor_id,
                last_time_block = excluded.last_time_block,
                total_drafted = excluded.total_drafted,
                total_failed = excluded.total_failed,
                total_skipped = excluded.total_skipped,
                updated_at = excluded.updated_at;
        """, (
            cursor.get("cursor_id", self.CURSOR_ID),
            cursor.get("last_sequence_order", 0),
            cursor.get("last_professor_id"),
            cursor.get("last_time_block"),
            cursor.get("total_drafted", 0),
            cursor.get("total_failed", 0),
            cursor.get("total_skipped", 0),
            now_str
        ))
        repo.conn.commit()

        # Write to JSON file
        try:
            STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(cursor, f, indent=2)
        except Exception:
            pass

    def get_next_candidates(
        self,
        repo: OutreachRepository,
        batch_size: int = 10,
        time_block: Optional[str] = None,
        from_beginning: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Retrieve next eligible professors strictly in workbook order.
        Resumes from cursor position unless from_beginning is True.
        """
        cursor = self.get_cursor(repo)
        start_order = 0 if from_beginning else cursor.get("last_sequence_order", 0)

        query = """
            SELECT p.*
            FROM professors p
            WHERE p.email IS NOT NULL AND p.email != ''
              AND p.outreach_status NOT IN ('SUPPRESSED', 'SENT', 'SIMULATED_SENT')
              AND p.professor_id NOT IN (
                  SELECT professor_id FROM outreach_drafts WHERE draft_id LIKE 'aidraft_%'
              )
        """
        params: List[Any] = []

        if not from_beginning and start_order > 0:
            query += " AND (p.time_block_order > ? OR p.time_block_order IS NULL)"
            params.append(start_order)

        if time_block:
            query += " AND p.time_block = ?"
            params.append(time_block)

        query += " ORDER BY p.time_block ASC, p.time_block_order ASC, p.qs_rank ASC LIMIT ?;"
        params.append(batch_size)

        cur = repo.conn.cursor()
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]

        # Filter out any suppressed contacts dynamically
        eligible = []
        for r in rows:
            is_supp, _ = autonomous_queue_runner.is_suppressed_or_excluded(repo, r["email"])
            if not is_supp:
                eligible.append(r)

        return eligible

    def stage_ai_draft(
        self,
        repo: OutreachRepository,
        draft_payload: Dict[str, Any],
        actor: str = "ANTIGRAVITY_AGENT"
    ) -> Dict[str, Any]:
        """
        Validate and import an AI-authored draft, then advance the durable cursor.
        """
        prof_id = draft_payload.get("professor_id")
        prof = repo.get_professor(prof_id)
        if not prof:
            prof = repo.get_professor_by_email(draft_payload.get("recipient_email"))

        import_res = self.importer.import_drafts([draft_payload], repo, actor=actor)

        cursor = self.get_cursor(repo)
        if import_res["imported_count"] > 0:
            cursor["total_drafted"] = cursor.get("total_drafted", 0) + 1
            if prof:
                cursor["last_sequence_order"] = max(cursor.get("last_sequence_order", 0), prof.get("time_block_order") or 0)
                cursor["last_professor_id"] = prof["professor_id"]
                cursor["last_time_block"] = prof.get("time_block") or cursor.get("last_time_block")
            self.save_cursor(repo, cursor)
            return {"success": True, "result": import_res["results"][0]}
        else:
            cursor["total_failed"] = cursor.get("total_failed", 0) + 1
            self.save_cursor(repo, cursor)
            # Update professor status to FAILED_VALIDATION
            if prof:
                err = import_res["results"][0].get("error", "Unknown validation error")
                repo.update_professor_status(prof["professor_id"], "FAILED_VALIDATION", notes=err)
            return {"success": False, "error": import_res["results"][0].get("error")}

    def mark_needs_research(
        self,
        repo: OutreachRepository,
        professor_id: str,
        reason: str
    ) -> None:
        """Mark professor as needing manual research enrichment before drafting."""
        repo.update_professor_status(
            professor_id,
            "NEEDS_RESEARCH",
            notes=f"Insufficient workbook evidence: {reason}"
        )
        cursor = self.get_cursor(repo)
        cursor["total_skipped"] = cursor.get("total_skipped", 0) + 1
        self.save_cursor(repo, cursor)

    def get_progress_metrics(self, repo: OutreachRepository) -> Dict[str, Any]:
        """Produce full progress accounting across the workbook prospects."""
        cur = repo.conn.cursor()

        total_prospects = cur.execute("SELECT COUNT(*) FROM professors;").fetchone()[0]
        verified_candidates = cur.execute(
            "SELECT COUNT(*) FROM professors WHERE email IS NOT NULL AND email != '' AND outreach_status != 'SUPPRESSED';"
        ).fetchone()[0]

        ai_drafts_count = cur.execute(
            "SELECT COUNT(*) FROM outreach_drafts WHERE draft_id LIKE 'aidraft_%';"
        ).fetchone()[0]

        queued_pending = cur.execute(
            "SELECT COUNT(*) FROM outreach_queue WHERE status = 'PENDING';"
        ).fetchone()[0]

        sent_count = cur.execute(
            "SELECT COUNT(*) FROM outreach_queue WHERE status IN ('SENT', 'SIMULATED_SENT');"
        ).fetchone()[0]

        suppressed_count = cur.execute(
            "SELECT COUNT(*) FROM professors WHERE outreach_status = 'SUPPRESSED';"
        ).fetchone()[0]

        needs_research = cur.execute(
            "SELECT COUNT(*) FROM professors WHERE outreach_status = 'NEEDS_RESEARCH';"
        ).fetchone()[0]

        failed_validation = cur.execute(
            "SELECT COUNT(*) FROM professors WHERE outreach_status = 'FAILED_VALIDATION';"
        ).fetchone()[0]

        remaining_to_draft = cur.execute("""
            SELECT COUNT(*) FROM professors
            WHERE email IS NOT NULL AND email != ''
              AND outreach_status NOT IN ('SUPPRESSED', 'SENT', 'SIMULATED_SENT', 'DRAFT_READY')
              AND professor_id NOT IN (SELECT professor_id FROM outreach_drafts WHERE draft_id LIKE 'aidraft_%');
        """).fetchone()[0]

        cursor = self.get_cursor(repo)

        return {
            "total_workbook_prospects": total_prospects,
            "verified_candidates": verified_candidates,
            "ai_drafts_completed": ai_drafts_count,
            "queued_pending": queued_pending,
            "dispatched_count": sent_count,
            "suppressed_count": suppressed_count,
            "needs_research_count": needs_research,
            "failed_validation_count": failed_validation,
            "remaining_to_draft": remaining_to_draft,
            "cursor_state": {
                "last_sequence_order": cursor.get("last_sequence_order", 0),
                "last_professor_id": cursor.get("last_professor_id"),
                "last_time_block": cursor.get("last_time_block"),
                "total_drafted": cursor.get("total_drafted", 0)
            }
        }


# Singleton instance
batch_ai_drafter = BatchAIDrafter()
