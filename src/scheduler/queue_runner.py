"""
Autonomous Outreach Queue Runner & Time-Block Dispatcher.
Enforces 13 IST regional time blocks, 90-minute sending windows,
5-message batches with 1-minute cooling breaks, queue persistence,
crash recovery, duplicate protection, dual PDF attachments,
and strict suppression of reply-received and institutional contacts.
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from zoneinfo import ZoneInfo

from config.settings import settings
from src.database.models import OutreachDraft, OutreachQueueItem, Professor, EmailMessage
from src.database.repository import OutreachRepository
from src.intelligence.personalization import PersonalizationEngine
from src.mail.sending_gateway import SendingGateway
from src.security.permissions import SecurityRole
from src.security.audit_logger import audit_logger


# Authoritative 6 Confirmed Reply-Received Contacts (Must not receive new initial outreach)
REPLY_RECEIVED_EXCLUSIONS: List[Tuple[str, str, str, str]] = [
    ("supp_han_fan_tum", "han.fan@tum.de", "REPLY_RECEIVED", "Han Fan (TUM) confirmed reply received; excluded from initial outreach"),
    ("supp_hojin_lee_tum", "hojin.lee@tum.de", "REPLY_RECEIVED", "Hojin Lee (TUM) confirmed reply received; excluded from initial outreach"),
    ("supp_lukas_oehler_tum", "lukas.oehler@tum.de", "REPLY_RECEIVED", "Lukas Oehler (TUM) confirmed reply received; excluded from initial outreach"),
    ("supp_tjtkoski_kth", "tjtkoski@kth.se", "REPLY_RECEIVED", "Timo Koski (KTH) confirmed reply received; excluded from initial outreach"),
    ("supp_fontina_petrakopoulou_tub", "fontina.petrakopoulou@tu-berlin.de", "REPLY_RECEIVED", "Fontina Petrakopoulou (TU Berlin) confirmed reply received; excluded from initial outreach"),
    ("supp_info_sitegeist", "info@sitegeist-robotics.com", "REPLY_RECEIVED", "Sitegeist Robotics confirmed reply received; excluded from initial outreach"),
]

# Prior external contacts
PRIOR_CONTACT_EXCLUSIONS: List[Tuple[str, str, str, str]] = [
    ("supp_kaikit_wong_ucl", "k.wong@ucl.ac.uk", "PREVIOUSLY_CONTACTED", "Prof. Kai-Kit Wong (UCL) already contacted; excluded from fresh outreach"),
    ("supp_kaikit_wong_alt", "kai-kit.wong@ucl.ac.uk", "PREVIOUSLY_CONTACTED", "Prof. Kai-Kit Wong (UCL) alternative email alias"),
    ("supp_chenshu_wu_hku", "cwu@cs.hku.hk", "PREVIOUSLY_CONTACTED", "Prof. Chenshu Wu (HKU) already contacted; excluded from fresh outreach"),
    ("supp_chenshu_wu_rutgers", "chenshu.wu@rutgers.edu", "PREVIOUSLY_CONTACTED", "Prof. Chenshu Wu (Rutgers) alternative institutional email"),
]

# 13 Regional IST Time Blocks with 90-Minute Dispatch Windows
TIME_BLOCK_WINDOWS: Dict[str, Dict[str, Any]] = {
    "BLOCK_01": {
        "name": "New Zealand",
        "start_ist": "01:00",
        "end_ist": "02:30",
        "local_target": "09:00 NZDT (UTC+13)",
        "start_time": dtime(1, 0),
        "end_time": dtime(2, 30),
    },
    "BLOCK_02": {
        "name": "Australia Eastern Daylight",
        "start_ist": "03:00",
        "end_ist": "04:30",
        "local_target": "09:00 AEDT (UTC+11)",
        "start_time": dtime(3, 0),
        "end_time": dtime(4, 30),
    },
    "BLOCK_03": {
        "name": "Australia Central & Queensland",
        "start_ist": "04:00",
        "end_ist": "05:30",
        "local_target": "09:00 ACDT/AEST (UTC+10.5 / +10)",
        "start_time": dtime(4, 0),
        "end_time": dtime(5, 30),
    },
    "BLOCK_04": {
        "name": "Japan & South Korea",
        "start_ist": "05:00",
        "end_ist": "06:30",
        "local_target": "09:00 JST/KST (UTC+9)",
        "start_time": dtime(5, 0),
        "end_time": dtime(6, 30),
    },
    "BLOCK_05": {
        "name": "Greater China, Singapore, Malaysia & WA",
        "start_ist": "06:00",
        "end_ist": "07:30",
        "local_target": "09:00 CST/HKT/SGT/AWST (UTC+8)",
        "start_time": dtime(6, 0),
        "end_time": dtime(7, 30),
    },
    "BLOCK_06": {
        "name": "Middle East & Russia",
        "start_ist": "11:00",
        "end_ist": "12:30",
        "local_target": "09:00 AST/MSK (UTC+3)",
        "start_time": dtime(11, 0),
        "end_time": dtime(12, 30),
    },
    "BLOCK_07": {
        "name": "Continental Europe (CET)",
        "start_ist": "13:00",
        "end_ist": "14:30",
        "local_target": "09:00 CET (UTC+1)",
        "start_time": dtime(13, 0),
        "end_time": dtime(14, 30),
    },
    "BLOCK_08": {
        "name": "United Kingdom & Ireland (GMT)",
        "start_ist": "14:00",
        "end_ist": "15:30",
        "local_target": "09:00 GMT (UTC+0)",
        "start_time": dtime(14, 0),
        "end_time": dtime(15, 30),
    },
    "BLOCK_09": {
        "name": "Latin America (Argentina & Chile)",
        "start_ist": "17:00",
        "end_ist": "18:30",
        "local_target": "09:00 ART/CLST (UTC-3)",
        "start_time": dtime(17, 0),
        "end_time": dtime(18, 30),
    },
    "BLOCK_10": {
        "name": "US & Canada Eastern (EDT)",
        "start_ist": "18:00",
        "end_ist": "19:30",
        "local_target": "09:00 EDT (UTC-4)",
        "start_time": dtime(18, 0),
        "end_time": dtime(19, 30),
    },
    "BLOCK_11": {
        "name": "US Central (CDT)",
        "start_ist": "19:00",
        "end_ist": "20:30",
        "local_target": "09:00 CDT (UTC-5)",
        "start_time": dtime(19, 0),
        "end_time": dtime(20, 30),
    },
    "BLOCK_12": {
        "name": "Canada Mountain (MDT)",
        "start_ist": "20:00",
        "end_ist": "21:30",
        "local_target": "09:00 MDT (UTC-6)",
        "start_time": dtime(20, 0),
        "end_time": dtime(21, 30),
    },
    "BLOCK_13": {
        "name": "US & Canada Pacific (PDT)",
        "start_ist": "21:00",
        "end_ist": "22:30",
        "local_target": "09:00 PDT (UTC-7)",
        "start_time": dtime(21, 0),
        "end_time": dtime(22, 30),
    },
}


SCHEDULER_LOCK_PATH = settings.DATA_DIR / "scheduler.lock"


def is_process_running(pid: int) -> bool:
    """Check if process with given PID is still active."""
    import os
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if process:
            kernel32.CloseHandle(process)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class AutonomousQueueRunner:
    """
    Autonomous Queue Runner managing durable outreach drafts and timed dispatch.
    Decouples AI draft preparation from headless background sending.
    """

    def __init__(self, engine: Optional[PersonalizationEngine] = None):
        self.personalization = engine or PersonalizationEngine()
        self.ist_tz = ZoneInfo("Asia/Kolkata")

    def acquire_scheduler_lock(self) -> bool:
        """Enforce single active scheduler instance using durable lock file."""
        import os
        SCHEDULER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        if SCHEDULER_LOCK_PATH.exists():
            try:
                with open(SCHEDULER_LOCK_PATH, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                active_pid = lock_data.get("pid")
                if active_pid and is_process_running(active_pid) and active_pid != os.getpid():
                    raise RuntimeError(
                        f"Another scheduler instance is currently running under PID {active_pid}. Single-instance policy enforced."
                    )
            except (json.JSONDecodeError, OSError):
                pass

        with open(SCHEDULER_LOCK_PATH, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "locked_at": datetime.now(timezone.utc).isoformat()}, f)
        return True

    def release_scheduler_lock(self) -> None:
        """Release the single-instance scheduler lock."""
        try:
            if SCHEDULER_LOCK_PATH.exists():
                SCHEDULER_LOCK_PATH.unlink()
        except Exception:
            pass

    def seed_reply_received_suppressions(self, repo: OutreachRepository) -> int:
        """
        Seed the 6 authoritative reply-received contacts and prior contacts into suppression list.
        Removes any unsupported classifications (e.g. Bodner / Liang).
        """
        # Clean up unsupported classifications
        repo.conn.execute("DELETE FROM suppression_list WHERE email IN ('bodner@mit.edu', 'pliang@cs.stanford.edu');")
        repo.conn.execute("""
            UPDATE professors 
            SET outreach_status = 'NEW',
                notes = REPLACE(REPLACE(notes, ' | REPLY_RECEIVED: Prof. Abigail Bodner (MIT) prior engagement / reply received; excluded from fresh outreach', ''),
                                ' | REPLY_RECEIVED: Prof. Percy Liang (Stanford) prior engagement / reply received; excluded from fresh outreach', '')
            WHERE email IN ('bodner@mit.edu', 'pliang@cs.stanford.edu') AND outreach_status = 'SUPPRESSED';
        """)

        seeded = 0
        all_exclusions = REPLY_RECEIVED_EXCLUSIONS + PRIOR_CONTACT_EXCLUSIONS
        for supp_id, email, reason, notes in all_exclusions:
            repo.conn.execute("""
                INSERT INTO suppression_list (suppression_id, email, domain, reason, notes, created_at)
                VALUES (?, ?, NULL, ?, ?, datetime('now'))
                ON CONFLICT(suppression_id) DO UPDATE SET
                    reason = excluded.reason,
                    notes = excluded.notes;
            """, (supp_id, email.lower(), reason, notes))
            seeded += 1
        repo.conn.commit()
        return seeded

    def is_suppressed_or_excluded(self, repo: OutreachRepository, email: str) -> Tuple[bool, Optional[str]]:
        """
        Check if an email is suppressed in repository or matches the 6 confirmed replied contacts / prior contacts.
        """
        if not email or not email.strip():
            return True, "BLANK_EMAIL: Missing email address"

        norm_email = email.strip().lower()
        all_exclusions = REPLY_RECEIVED_EXCLUSIONS + PRIOR_CONTACT_EXCLUSIONS
        for _, ex_email, reason, notes in all_exclusions:
            if norm_email == ex_email.lower():
                return True, f"{reason}: {notes}"

        return repo.is_suppressed(norm_email)

    def is_window_active(self, time_block_id: str, current_dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Evaluate whether the current time in IST is within the 90-minute window of the block.
        """
        block_info = TIME_BLOCK_WINDOWS.get(time_block_id)
        if not block_info:
            return False, f"Unknown time block: {time_block_id}"

        now_ist = (current_dt or datetime.now(self.ist_tz)).astimezone(self.ist_tz)
        current_time = now_ist.time()

        start_t = block_info["start_time"]
        end_t = block_info["end_time"]

        is_active = start_t <= current_time <= end_t
        desc = (
            f"Block {time_block_id} ({block_info['name']}) window is "
            f"{block_info['start_ist']} - {block_info['end_ist']} IST. "
            f"Current IST is {now_ist.strftime('%H:%M:%S')}. Active={is_active}."
        )
        return is_active, desc

    def build_queue_from_professors(
        self,
        repo: OutreachRepository,
        time_block: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate personalized drafts and enqueue unsuppressed professors into the durable queue.
        Preserves workbook sequence order, research overlap, and time-block assignments.
        """
        # Ensure reply-received contacts are firmly suppressed
        self.seed_reply_received_suppressions(repo)

        query = """
            SELECT * FROM professors
            WHERE email IS NOT NULL AND email != ''
              AND outreach_status NOT IN ('SUPPRESSED', 'SENT', 'SIMULATED_SENT', 'REPLIED', 'OPT_OUT', 'DECLINED', 'BOUNCED')
        """
        params: List[Any] = []
        if time_block:
            query += " AND time_block = ?"
            params.append(time_block)

        query += " ORDER BY time_block ASC, time_block_order ASC, qs_rank ASC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cursor = repo.conn.execute(query, params)
        profs = [dict(row) for row in cursor.fetchall()]

        enqueued_count = 0
        suppressed_count = 0
        skipped_count = 0

        # Query existing queue IDs to avoid duplicates
        existing_queue_prof_ids = {
            row["professor_id"]
            for row in repo.conn.execute("SELECT professor_id FROM outreach_queue;").fetchall()
        }

        # Track batch numbers per time block (5 messages per batch)
        block_batch_counters: Dict[str, int] = {}
        block_item_counters: Dict[str, int] = {}

        for prof in profs:
            prof_id = prof["professor_id"]
            email = prof["email"]
            block_id = prof.get("time_block") or "BLOCK_01"

            if prof_id in existing_queue_prof_ids:
                skipped_count += 1
                continue

            # Check suppression
            is_supp, reason = self.is_suppressed_or_excluded(repo, email)
            if is_supp:
                repo.update_professor_status(prof_id, "SUPPRESSED", reason)
                suppressed_count += 1
                continue

            # Generate personalized draft with exact required subject line
            draft = self.personalization.generate_draft(prof)
            # Guarantee exact subject structure
            draft.subject = PersonalizationEngine.format_subject_line(
                prof.get("research_areas", ""),
                prof.get("research_overlap", "")
            )
            draft.status = "APPROVED"  # Pre-approved for queued dispatch
            repo.save_outreach_draft(draft)

            # Assign batch number (5 items per batch within this block)
            item_count = block_item_counters.get(block_id, 0)
            batch_num = (item_count // settings.BATCH_SIZE) + 1
            block_item_counters[block_id] = item_count + 1

            window_info = TIME_BLOCK_WINDOWS.get(block_id, {})
            start_ist = window_info.get("start_ist", "08:00")
            end_ist = window_info.get("end_ist", "09:30")

            queue_id = f"q_{prof.get('time_block_order', 0):04d}_{prof_id[5:15]}"
            queue_item = OutreachQueueItem(
                queue_id=queue_id,
                professor_id=prof_id,
                draft_id=draft.draft_id,
                time_block_id=block_id,
                window_start_ist=start_ist,
                window_end_ist=end_ist,
                sequence_order=prof.get("time_block_order") or enqueued_count + 1,
                status="PENDING",
                batch_number=batch_num
            )
            repo.enqueue_outreach(queue_item)
            existing_queue_prof_ids.add(prof_id)
            enqueued_count += 1

        repo.conn.commit()

        audit_logger.log_event(
            event_type="OUTREACH_QUEUE_BUILT",
            actor="SYSTEM_SCHEDULER",
            entity_type="QUEUE",
            entity_id=time_block or "ALL_BLOCKS",
            details={
                "enqueued_count": enqueued_count,
                "suppressed_count": suppressed_count,
                "skipped_count": skipped_count,
            }
        )

        return {
            "enqueued_count": enqueued_count,
            "suppressed_count": suppressed_count,
            "skipped_count": skipped_count,
            "total_candidates": len(profs)
        }

    def dispatch_queue(
        self,
        repo: OutreachRepository,
        gateway: Optional[SendingGateway] = None,
        time_block: Optional[str] = None,
        max_messages: Optional[int] = None,
        dry_run: bool = True,
        force_window: bool = False,
        simulated_cooling: bool = True,
        operator_id: str = "Harshith N (Autonomous Scheduler)",
        allow_templates: bool = False
    ) -> Dict[str, Any]:
        """
        Execute queue dispatch subject to:
        1. Single-instance process lock.
        2. Crash recovery on startup (AMBIGUOUS_DELIVERY protection).
        3. Conservative compliance cap (default 25 messages/day operator threshold).
        4. 90-minute time-block window check.
        5. Cadence: 5 messages, followed by 1-minute cooling break.
        6. Strict AI authoring gate: only approved 'aidraft_' records can reach gateway.
        7. Dual authoritative PDF attachments with preserved recipient filenames.
        8. Absolute suppression enforcement.
        """
        self.acquire_scheduler_lock()
        try:
            gw = gateway or SendingGateway()
            limit = max_messages or settings.DAILY_MAX_OUTREACH

            # 1. Crash recovery: rescue any items stranded in IN_PROGRESS
            recovered = repo.recover_crashed_queue_items()

            # 2. Daily sending count check
            today_date = datetime.now(self.ist_tz).strftime("%Y-%m-%d")
            daily_sent_row = repo.conn.execute("""
                SELECT COUNT(*) FROM outreach_queue
                WHERE status IN ('SENT', 'SIMULATED_SENT')
                  AND substr(dispatched_at, 1, 10) = ?;
            """, (today_date,)).fetchone()
            daily_sent_today = daily_sent_row[0] if daily_sent_row else 0

            remaining_daily_budget = max(0, settings.DAILY_MAX_OUTREACH - daily_sent_today)
            actual_limit = min(limit, remaining_daily_budget)

            if actual_limit <= 0:
                return {
                    "success": False,
                    "reason": f"Daily outreach cap reached ({daily_sent_today}/{settings.DAILY_MAX_OUTREACH} messages dispatched today)",
                    "dispatched_count": 0,
                    "recovered_crashed": recovered,
                    "items": []
                }

            # 3. Retrieve pending queue items
            pending_items = repo.list_queue_items(status="PENDING", time_block=time_block, limit=actual_limit)
            if not pending_items:
                return {
                    "success": True,
                    "message": "No pending items in queue for target criteria.",
                    "dispatched_count": 0,
                    "recovered_crashed": recovered,
                    "items": []
                }

            dispatched_items = []
            messages_in_current_batch = 0

            for item in pending_items:
                q_id = item["queue_id"]
                draft_id = item["draft_id"]
                prof_id = item["professor_id"]
                recipient = item["recipient_email"]
                block_id = item["time_block_id"]

                # Strict AI Draft Gate: never dispatch placeholder, template, or blank draft
                is_valid_prefix = draft_id.startswith("aidraft_") or (allow_templates and draft_id.startswith("draft_"))
                if not is_valid_prefix or not item.get("draft_body"):
                    repo.update_queue_status(
                        q_id,
                        "HELD_NON_AI_DRAFT",
                        error_message="Disallowing non-AI, placeholder, or empty draft from dispatch."
                    )
                    audit_logger.log_event(
                        event_type="DISPATCH_BLOCKED_NON_AI_DRAFT",
                        actor="SYSTEM_SCHEDULER",
                        entity_type="QUEUE_ITEM",
                        entity_id=q_id,
                        details={"draft_id": draft_id, "recipient": recipient}
                    )
                    continue

                # Window check
                if not force_window:
                    is_active, reason = self.is_window_active(block_id)
                    if not is_active:
                        # Outside active 90-minute window; hold for proper window
                        continue

                # Suppression check
                is_supp, supp_reason = self.is_suppressed_or_excluded(repo, recipient)
                if is_supp:
                    repo.update_queue_status(q_id, "SUPPRESSED", error_message=supp_reason)
                    repo.update_professor_status(prof_id, "SUPPRESSED", supp_reason)
                    continue

                # Mark IN_PROGRESS (crash recovery anchor)
                repo.update_queue_status(q_id, "IN_PROGRESS")

                # Dispatch via Gateway
                res = gw.dispatch_initial_batch(
                    repo=repo,
                    draft_ids=[draft_id],
                    actor=SecurityRole.HUMAN_OPERATOR,
                    operator_id=operator_id,
                    dry_run=dry_run
                )

                dispatch_result = res["results"][0] if res["results"] else {}
                status_label = dispatch_result.get("status", "SIMULATED_SENT" if dry_run else "SENT")
                message_id = dispatch_result.get("message_id", f"msg_sim_{draft_id[6:]}")

                # Update queue item to dispatched
                now_str = datetime.now(timezone.utc).isoformat()
                repo.update_queue_status(q_id, status_label, dispatched_at=now_str)

                dispatched_items.append({
                    "queue_id": q_id,
                    "professor_id": prof_id,
                    "recipient": recipient,
                    "professor_name": item["professor_name"],
                    "university": item["university_name"],
                    "subject": item["draft_subject"],
                    "time_block": block_id,
                    "status": status_label,
                    "message_id": message_id
                })

                messages_in_current_batch += 1

                # Cadence check: every 5 messages, take a 1-minute cooling break
                if messages_in_current_batch >= settings.BATCH_SIZE and len(dispatched_items) < len(pending_items):
                    cooling_seconds = settings.COOLING_BREAK_SECONDS
                    audit_logger.log_event(
                        event_type="CADENCE_COOLING_BREAK",
                        actor="SYSTEM_SCHEDULER",
                        entity_type="QUEUE_CADENCE",
                        entity_id=block_id,
                        details={
                            "messages_sent_in_batch": messages_in_current_batch,
                            "cooling_seconds": cooling_seconds,
                            "simulated": simulated_cooling
                        }
                    )
                    if not simulated_cooling:
                        time.sleep(cooling_seconds)
                    messages_in_current_batch = 0  # Reset batch counter for next set of 5

                if len(dispatched_items) >= actual_limit:
                    break

            repo.conn.commit()

            return {
                "success": True,
                "dispatched_count": len(dispatched_items),
                "recovered_crashed": recovered,
                "dry_run": dry_run,
                "items": dispatched_items
            }
        finally:
            self.release_scheduler_lock()

    def get_queue_diagnostics(self, repo: OutreachRepository) -> Dict[str, Any]:
        """
        Produce a diagnostic summary of the queue state across all 13 blocks.
        """
        cur = repo.conn.cursor()
        cur.execute("""
            SELECT time_block_id, status, COUNT(*)
            FROM outreach_queue
            GROUP BY time_block_id, status
            ORDER BY time_block_id, status;
        """)
        breakdown: Dict[str, Dict[str, int]] = {}
        for row in cur.fetchall():
            b_id, status, cnt = row[0], row[1], row[2]
            if b_id not in breakdown:
                breakdown[b_id] = {}
            breakdown[b_id][status] = cnt

        total_queued = cur.execute("SELECT COUNT(*) FROM outreach_queue;").fetchone()[0]
        pending_total = cur.execute("SELECT COUNT(*) FROM outreach_queue WHERE status = 'PENDING';").fetchone()[0]
        sent_total = cur.execute("SELECT COUNT(*) FROM outreach_queue WHERE status IN ('SENT', 'SIMULATED_SENT');").fetchone()[0]
        supp_total = cur.execute("SELECT COUNT(*) FROM outreach_queue WHERE status = 'SUPPRESSED';").fetchone()[0]

        return {
            "total_queued": total_queued,
            "pending_count": pending_total,
            "sent_count": sent_total,
            "suppressed_count": supp_total,
            "time_block_breakdown": breakdown
        }


# Singleton instance
autonomous_queue_runner = AutonomousQueueRunner()
