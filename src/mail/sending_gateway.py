"""
Outbound Sending Gateway and Compliance Enforcement Engine.
Enforces institutional permissions, rate limits, suppression safeguards, and
application-level separation of human-only reply transmissions.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from config.settings import settings
from src.database.models import EmailMessage
from src.database.repository import OutreachRepository
from src.mail.smtp_client import smtp_client
from src.security.permissions import (
    SecurityRole, SecurityAction, require_permission,
    assert_human_only_transmission, validate_campaign_preflight, PermissionDeniedError
)
from src.security.audit_logger import audit_logger


class SendingGateway:
    """Controls outgoing email transmissions with strict safety boundaries."""

    def __init__(self, smtp=None):
        self.smtp = smtp or smtp_client

    def dispatch_initial_batch(
        self,
        repo: OutreachRepository,
        draft_ids: List[str],
        actor: SecurityRole,
        operator_id: Optional[str] = None,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Dispatch authorized initial first-contact emails.
        Guarded by institutional preflight checks, rate limits, and suppression.
        """
        require_permission(SecurityAction.DISPATCH_INITIAL_BATCH, actor)

        # Preflight validation
        validate_campaign_preflight(
            gateway_enabled=settings.GATEWAY_ENABLED,
            campaign_authorized=settings.CAMPAIGN_AUTHORIZED,
            csc_approved=settings.IITD_CSC_APPROVED,
            emergency_stop=settings.EMERGENCY_STOP,
            dry_run=dry_run
        )

        results = []
        for draft_id in draft_ids:
            cursor = repo.conn.execute("SELECT * FROM outreach_drafts WHERE draft_id = ?;", (draft_id,))
            draft = cursor.fetchone()
            if not draft:
                continue

            recipient = draft["recipient_email"]
            prof_id = draft["professor_id"]

            # Suppression Guard
            is_supp, reason = repo.is_suppressed(recipient)
            if is_supp:
                repo.update_professor_status(prof_id, "SUPPRESSED", f"Dispatch aborted: {reason}")
                results.append({
                    "draft_id": draft_id,
                    "recipient": recipient,
                    "status": "SUPPRESSED_ABORTED",
                    "reason": reason
                })
                continue

            # Transmission (Live or Dry-Run)
            res = self.smtp.send_email(
                recipient=recipient,
                subject=draft["subject"],
                body_text=draft["body_text"],
                dry_run=dry_run,
                actor=actor.value,
                operator_id=operator_id
            )

            message_id = res["message_id"]
            thread_id = repo.get_or_create_thread(
                thread_id=f"th_{draft_id[6:]}",
                professor_id=prof_id,
                subject=draft["subject"]
            )

            # Store Outbound Email Message
            email_msg = EmailMessage(
                message_id=message_id,
                thread_id=thread_id,
                professor_id=prof_id,
                direction="OUTBOUND",
                sender=settings.IITD_EMAIL_USER,
                recipient=recipient,
                subject=draft["subject"],
                body=draft["body_text"],
                sent_received_at=datetime.now(timezone.utc).isoformat()
            )
            repo.save_email_message(email_msg)

            # Update Draft and Professor Status
            status_label = "SENT" if not dry_run else "SIMULATED_SENT"
            repo.conn.execute("""
                UPDATE outreach_drafts
                SET status = ?, dispatched_at = datetime('now')
                WHERE draft_id = ?;
            """, (status_label, draft_id))

            repo.update_professor_status(prof_id, "SENT" if not dry_run else "DRAFT_READY")
            results.append({
                "draft_id": draft_id,
                "message_id": message_id,
                "recipient": recipient,
                "status": status_label
            })

        return {"dispatched_count": len(results), "results": results}

    def human_send_reply(
        self,
        repo: OutreachRepository,
        reply_draft_id: str,
        actor: SecurityRole,
        operator_id: str,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        HUMAN-ONLY action to transmit an approved reply to a professor.
        Autonomous AI agents are strictly forbidden by code permissions from calling this.
        """
        assert_human_only_transmission(SecurityAction.SEND_REPLY_TRANSMISSION, actor, operator_id)

        if settings.EMERGENCY_STOP:
            raise PermissionDeniedError("TRANSMISSION BLOCKED: Emergency stop is active.")

        cursor = repo.conn.execute("SELECT * FROM reply_drafts WHERE reply_draft_id = ?;", (reply_draft_id,))
        reply_draft = cursor.fetchone()
        if not reply_draft:
            raise ValueError(f"Reply draft {reply_draft_id} not found.")

        prof_id = reply_draft["professor_id"]
        prof = repo.get_professor(prof_id)
        if not prof:
            raise ValueError(f"Professor {prof_id} not found.")

        recipient = prof["email"]
        is_supp, reason = repo.is_suppressed(recipient)
        if is_supp:
            raise PermissionDeniedError(f"Cannot send reply: Recipient {recipient} is suppressed ({reason}).")

        # Fetch original message for threading headers
        orig_msg = repo.get_email_message(reply_draft["reply_to_message_id"])
        in_reply_to = orig_msg["message_id"] if orig_msg else None
        subject = f"Re: {orig_msg['subject']}" if orig_msg else "Re: Summer 2027 Research Internship"

        res = self.smtp.send_email(
            recipient=recipient,
            subject=subject,
            body_text=reply_draft["draft_body"],
            in_reply_to=in_reply_to,
            references=in_reply_to,
            dry_run=dry_run,
            actor=actor.value,
            operator_id=operator_id
        )

        message_id = res["message_id"]

        # Store Outbound Reply Message
        email_msg = EmailMessage(
            message_id=message_id,
            thread_id=reply_draft["thread_id"],
            professor_id=prof_id,
            direction="OUTBOUND",
            sender=settings.IITD_EMAIL_USER,
            recipient=recipient,
            subject=subject,
            body=reply_draft["draft_body"],
            in_reply_to=in_reply_to,
            references_header=in_reply_to,
            sent_received_at=datetime.now(timezone.utc).isoformat()
        )
        repo.save_email_message(email_msg)

        # Update Reply Draft Status
        repo.conn.execute("""
            UPDATE reply_drafts
            SET status = 'HUMAN_SENT',
                human_approved_at = datetime('now'),
                human_reviewer = ?,
                human_sent_at = datetime('now')
            WHERE reply_draft_id = ?;
        """, (operator_id, reply_draft_id))

        audit_logger.log_event(
            event_type="HUMAN_REPLY_TRANSMITTED",
            actor=actor.value,
            operator_id=operator_id,
            entity_type="REPLY_DRAFT",
            entity_id=reply_draft_id,
            details={"recipient": recipient, "message_id": message_id, "dry_run": dry_run}
        )

        return {
            "success": True,
            "reply_draft_id": reply_draft_id,
            "message_id": message_id,
            "recipient": recipient,
            "operator_id": operator_id,
            "dry_run": dry_run
        }

    def human_send_followup(
        self,
        repo: OutreachRepository,
        followup_draft_id: str,
        actor: SecurityRole,
        operator_id: str,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        HUMAN-ONLY action to transmit an approved follow-up email.
        Autonomous AI agents are strictly forbidden by code permissions from calling this.
        """
        assert_human_only_transmission(SecurityAction.SEND_FOLLOWUP_TRANSMISSION, actor, operator_id)

        if settings.EMERGENCY_STOP:
            raise PermissionDeniedError("TRANSMISSION BLOCKED: Emergency stop is active.")

        cursor = repo.conn.execute("SELECT * FROM followup_drafts WHERE followup_draft_id = ?;", (followup_draft_id,))
        fu_draft = cursor.fetchone()
        if not fu_draft:
            raise ValueError(f"Follow-up draft {followup_draft_id} not found.")

        prof_id = fu_draft["professor_id"]
        prof = repo.get_professor(prof_id)
        if not prof:
            raise ValueError(f"Professor {prof_id} not found.")

        recipient = prof["email"]
        is_supp, reason = repo.is_suppressed(recipient)
        if is_supp:
            raise PermissionDeniedError(f"Cannot send follow-up: Recipient {recipient} is suppressed ({reason}).")

        orig_msg = repo.get_email_message(fu_draft["original_message_id"])
        in_reply_to = orig_msg["message_id"] if orig_msg else None
        subject = f"Following up: Prospective Summer 2027 Research Internship — Harshith N (IIT Delhi)"

        res = self.smtp.send_email(
            recipient=recipient,
            subject=subject,
            body_text=fu_draft["draft_body"],
            in_reply_to=in_reply_to,
            references=in_reply_to,
            dry_run=dry_run,
            actor=actor.value,
            operator_id=operator_id
        )

        message_id = res["message_id"]

        thread_id = orig_msg["thread_id"] if orig_msg else f"th_{prof_id}"
        email_msg = EmailMessage(
            message_id=message_id,
            thread_id=thread_id,
            professor_id=prof_id,
            direction="OUTBOUND",
            sender=settings.IITD_EMAIL_USER,
            recipient=recipient,
            subject=subject,
            body=fu_draft["draft_body"],
            in_reply_to=in_reply_to,
            references_header=in_reply_to,
            sent_received_at=datetime.now(timezone.utc).isoformat()
        )
        repo.save_email_message(email_msg)

        repo.conn.execute("""
            UPDATE followup_drafts
            SET status = 'HUMAN_SENT',
                human_approved_at = datetime('now'),
                human_reviewer = ?,
                human_sent_at = datetime('now')
            WHERE followup_draft_id = ?;
        """, (operator_id, followup_draft_id))

        audit_logger.log_event(
            event_type="HUMAN_FOLLOWUP_TRANSMITTED",
            actor=actor.value,
            operator_id=operator_id,
            entity_type="FOLLOWUP_DRAFT",
            entity_id=followup_draft_id,
            details={"recipient": recipient, "message_id": message_id, "dry_run": dry_run}
        )

        return {
            "success": True,
            "followup_draft_id": followup_draft_id,
            "message_id": message_id,
            "recipient": recipient,
            "operator_id": operator_id,
            "dry_run": dry_run
        }


# Global sending gateway instance
sending_gateway = SendingGateway()
