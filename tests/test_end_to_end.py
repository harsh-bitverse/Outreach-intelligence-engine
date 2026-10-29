"""
End-to-End Integration Test for Outreach Intelligence Engine.
Validates the complete lifecycle: import, personalization, scheduling,
batch dispatch (dry-run), reply correlation, classification, contextual drafting,
and human-only reply approval.
"""

import tempfile
from datetime import date
from pathlib import Path
import openpyxl
import pytest
from src.database.connection import get_db, initialize_database
from src.database.repository import OutreachRepository, compute_durable_professor_id
from src.database.models import Professor, EmailMessage
from src.database.workbook_sync import DynamicWorkbookSync
from src.intelligence.personalization import PersonalizationEngine
from src.intelligence.reply_classifier import reply_classifier, ReplyCategory
from src.intelligence.reply_drafter import reply_drafter
from src.scheduler.time_blocks import time_block_scheduler
from src.mail.thread_matcher import ThreadMatcher
from src.mail.sending_gateway import SendingGateway
from src.security.permissions import SecurityRole, PermissionDeniedError


def test_full_engine_lifecycle():
    """Verify end-to-end workflow across all components."""
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_path = tmp_dir / "e2e_outreach.db"
        wb_path = tmp_dir / "e2e_professors.xlsx"

        # 1. Initialize SQLite Database with suppressions
        initialize_database(db_path)

        # 2. Build mock authoritative Excel workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Professor Database"
        ws.append([
            "QS Rank", "Processing Sequence", "University Name", "Country",
            "Professor Full Name", "Academic Title / Position", "Department / Faculty",
            "Official Institutional Email", "Email Verification Status",
            "Email Verification Source URL", "Official Faculty Profile URL",
            "Research Areas / Keywords", "Specific Research Overlap With My Interests",
            "Relevant Skill or Project From My CV", "Potential Software / Computational Contribution",
            "Research Evidence URL", "Date Verified", "Notes / Unresolved Issues"
        ])
        ws.append([
            1, 1, "Massachusetts Institute of Technology (MIT)", "United States",
            "Abigail Bodner", "Assistant Professor", "EECS",
            "bodner@mit.edu", "VERIFIED", "https://mit.edu/src",
            "https://mit.edu/profile", "SciML, physical modeling, fluid dynamics",
            "Scientific ML surrogates and PDE solving",
            "Neural Process Twin", "PyTorch surrogate modeling",
            "https://mit.edu/evidence", "2026-10-25", "Screened"
        ])
        wb.save(str(wb_path))
        wb.close()

        with get_db(db_path) as conn:
            repo = OutreachRepository(conn)
            sync = DynamicWorkbookSync(wb_path)

            # 3. Import Authoritative Workbook dynamically
            import_stats = sync.import_to_database(repo)
            assert import_stats["professors_imported"] == 1

            prof_id = compute_durable_professor_id("Massachusetts Institute of Technology (MIT)", "Abigail Bodner")
            prof = repo.get_professor(prof_id)
            assert prof is not None
            assert prof["email"] == "bodner@mit.edu"

            # 4. Generate Personalized 4-Paragraph Draft
            engine = PersonalizationEngine()
            draft = engine.generate_draft(prof)
            assert 130 <= draft.word_count <= 180
            assert "ch1251635@iitd.ac.in" in draft.body_text
            assert "linkedin.com" not in draft.body_text.lower()
            assert "github.com" not in draft.body_text.lower()

            repo.save_outreach_draft(draft)
            repo.update_professor_status(prof_id, "DRAFT_READY")

            # 5. Schedule Regional Time Blocks
            blocks = time_block_scheduler.generate_schedule_blocks_summary([prof], target_date=date(2027, 2, 1))
            us_east = next(b for b in blocks if b["block_id"] == "BLOCK_09_US_EAST")
            assert us_east["faculty_count"] == 1
            assigned_prof = us_east["professors"][0]
            assert "18:30:00 IST" in assigned_prof["scheduled_ist_time"]

            # 6. Human Operator Approves Draft
            repo.conn.execute("UPDATE outreach_drafts SET status = 'APPROVED' WHERE draft_id = ?;", (draft.draft_id,))
            repo.update_professor_status(prof_id, "APPROVED")

            # 7. Dispatch Batch in Dry-Run Mode
            gateway = SendingGateway()
            dispatch_res = gateway.dispatch_initial_batch(
                repo=repo,
                draft_ids=[draft.draft_id],
                actor=SecurityRole.HUMAN_OPERATOR,
                operator_id="Harshith N",
                dry_run=True
            )
            assert dispatch_res["dispatched_count"] == 1
            outbound_msg_id = dispatch_res["results"][0]["message_id"]

            # 8. Simulate Incoming Professor Reply
            incoming_subject = "Re: Prospective Summer 2027 Research Internship — Harshith N (IIT Delhi)"
            incoming_body = (
                "Hi Harshith,\n\n"
                "Thank you for reaching out. Your work on Neural Process Twin sounds very interesting. "
                "Let's schedule a call next week via Zoom to discuss. Here is my link: calendly.com/sample\n\n"
                "Best,\nAbigail"
            )
            incoming_msg_id = "msg_inbound_mit_01@mit.edu"

            # Correlate Thread
            thread_id, matched_prof_id = ThreadMatcher.correlate_incoming_message(
                repo=repo,
                sender_email="bodner@mit.edu",
                subject=incoming_subject,
                in_reply_to=f"<{outbound_msg_id}>"
            )
            assert thread_id is not None
            assert matched_prof_id == prof_id

            # Save Inbound Message
            repo.save_email_message(EmailMessage(
                message_id=incoming_msg_id,
                thread_id=thread_id,
                professor_id=prof_id,
                direction="INBOUND",
                sender="bodner@mit.edu",
                recipient="ch1251635@iitd.ac.in",
                subject=incoming_subject,
                body=incoming_body,
                in_reply_to=outbound_msg_id
            ))

            # 9. Classify Reply Category
            cat, conf, reasoning, action = reply_classifier.classify(incoming_subject, incoming_body)
            assert cat == ReplyCategory.MEETING_INVITE
            assert conf >= 0.90

            # Auto-update status to REPLIED and halt queue
            repo.update_professor_status(prof_id, "REPLIED", f"Received {cat} from professor")

            # 10. Generate Context-Aware Reply Draft
            incoming_msg_dict = repo.get_email_message(incoming_msg_id)
            reply_draft = reply_drafter.draft_reply(
                professor=prof,
                incoming_message=incoming_msg_dict,
                classification={"category": cat, "confidence": conf, "reasoning": reasoning}
            )
            assert reply_draft is not None
            assert reply_draft.status == "AWAITING_HUMAN_REVIEW"
            assert "Google Meet / Zoom / Teams" in reply_draft.draft_body
            assert "ch1251635@iitd.ac.in" in reply_draft.draft_body

            repo.save_reply_draft(reply_draft)

            # 11. Security Check: Autonomous AI cannot send reply!
            with pytest.raises(PermissionDeniedError):
                gateway.human_send_reply(
                    repo=repo,
                    reply_draft_id=reply_draft.reply_draft_id,
                    actor=SecurityRole.AUTONOMOUS_AI_ENGINE,
                    operator_id=None,
                    dry_run=True
                )

            # 12. Human Operator Approves & Sends Reply (Dry-Run)
            send_res = gateway.human_send_reply(
                repo=repo,
                reply_draft_id=reply_draft.reply_draft_id,
                actor=SecurityRole.HUMAN_OPERATOR,
                operator_id="Harshith N",
                dry_run=True
            )
            assert send_res["success"] is True
            assert send_res["operator_id"] == "Harshith N"

            # 13. Verify Database Status
            updated_reply = repo.conn.execute(
                "SELECT status, human_reviewer FROM reply_drafts WHERE reply_draft_id = ?;",
                (reply_draft.reply_draft_id,)
            ).fetchone()
            assert updated_reply["status"] == "HUMAN_SENT"
            assert updated_reply["human_reviewer"] == "Harshith N"
