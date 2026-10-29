"""
FastAPI REST API Routes for Outreach Intelligence Engine.
Provides hardened endpoints for operator authentication, cryptographic approval verification,
dashboard metrics, professor management, IST scheduling, human review inbox,
discrepancy audits, knowledge base management, and diagnostics.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body, Header, status
from pydantic import BaseModel, Field

from config.settings import settings
from src.database.connection import get_db
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.database.workbook_audit import WorkbookAuditor
from src.intelligence.personalization import PersonalizationEngine
from src.intelligence.reply_classifier import reply_classifier
from src.intelligence.reply_drafter import reply_drafter
from src.intelligence.knowledge_base import kb_manager
from src.scheduler.time_blocks import time_block_scheduler
from src.scheduler.timezone_engine import timezone_engine
from src.scheduler.missed_window_recovery import recovery_engine
from src.mail.imap_client import imap_client
from src.mail.smtp_client import smtp_client
from src.mail.sending_gateway import sending_gateway
from src.security.permissions import SecurityRole, SecurityAction, PermissionDeniedError
from src.security.sessions import (
    session_store,
    compute_draft_content_hash,
    SecuritySessionError,
    ApprovalTamperingError
)
from src.security.audit_logger import audit_logger


api_router = APIRouter(prefix="/api")
personalization_engine = PersonalizationEngine()
workbook_sync = DynamicWorkbookSync()
workbook_auditor = WorkbookAuditor()


# ------------------------------------------------------------------------------
# Request Schemas
# ------------------------------------------------------------------------------
class OperatorLoginRequest(BaseModel):
    operator_id: str = Field(..., description="Operator identifier (e.g. 'Harshith N')")
    passphrase: str = Field("Harshith-IITD-Summer2027", description="Operator secret passphrase")


class OperatorLogoutRequest(BaseModel):
    session_token: str = Field(..., description="Active session token to invalidate")


class HumanActionRequest(BaseModel):
    operator_id: str = Field(..., description="Verified Human Operator Identifier (e.g. 'Harshith N')")
    session_token: Optional[str] = Field(None, description="Validated Operator Session Token")
    approved_content_hash: Optional[str] = Field(None, description="Cryptographic SHA-256 hash of approved content")
    dry_run: bool = Field(True, description="When true, executes dry-run simulation without SMTP wire transmission")


class ManualSuppressionRequest(BaseModel):
    email: Optional[str] = None
    domain: Optional[str] = None
    reason: str = "MANUAL"
    notes: str = ""


# ------------------------------------------------------------------------------
# Operator Session Authentication
# ------------------------------------------------------------------------------
@api_router.post("/auth/operator-login")
def operator_login(req: OperatorLoginRequest):
    """
    Authenticate human operator and issue a cryptographically signed session token.
    Enforces server-side authentication for human-only sending capabilities.
    """
    try:
        session = session_store.create_session(
            operator_id=req.operator_id,
            secret_passphrase=req.passphrase
        )
        audit_logger.log_event(
            event_type="OPERATOR_AUTHENTICATED",
            actor=SecurityRole.HUMAN_OPERATOR.value,
            operator_id=req.operator_id,
            entity_type="OPERATOR_SESSION",
            entity_id=session.session_token[:12] + "...",
            details={"expires_at": session.expires_at}
        )
        return {
            "success": True,
            "operator_id": session.operator_id,
            "session_token": session.session_token,
            "expires_at": session.expires_at,
            "permissions": session.permissions
        }
    except SecuritySessionError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@api_router.post("/auth/operator-logout")
def operator_logout(req: OperatorLogoutRequest):
    """Terminate and invalidate active human operator session."""
    session_store.terminate_session(req.session_token)
    return {"success": True, "message": "Operator session terminated."}


# ------------------------------------------------------------------------------
# Metrics & System Health
# ------------------------------------------------------------------------------
@api_router.get("/metrics")
def get_metrics():
    """Retrieve operational dashboard statistics."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        metrics = repo.get_dashboard_metrics()
        metrics["gateway_enabled"] = settings.GATEWAY_ENABLED
        metrics["dry_run"] = settings.DRY_RUN
        metrics["emergency_stop"] = settings.EMERGENCY_STOP
        metrics["campaign_authorized"] = settings.CAMPAIGN_AUTHORIZED
        metrics["csc_approved"] = settings.IITD_CSC_APPROVED
        return metrics


@api_router.get("/health")
def get_health():
    """System health check and diagnostic overview."""
    return {
        "status": "OPERATIONAL",
        "emergency_stop": settings.EMERGENCY_STOP,
        "gateway_enabled": settings.GATEWAY_ENABLED,
        "dry_run": settings.DRY_RUN,
        "master_workbook": str(settings.MASTER_WORKBOOK_PATH),
        "db_path": str(settings.SQLITE_DB_PATH)
    }


# ------------------------------------------------------------------------------
# Professors & Prospects
# ------------------------------------------------------------------------------
@api_router.get("/professors")
def list_professors(
    university: Optional[str] = None,
    country: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """Query searchable professor prospects."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        items = repo.list_professors(university, country, status, limit, offset)
        total = repo.count_professors(status)
        return {"total": total, "limit": limit, "offset": offset, "professors": items}


@api_router.get("/professors/{professor_id}")
def get_professor_detail(professor_id: str):
    """Retrieve detailed profile and existing drafts for a professor."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        prof = repo.get_professor(professor_id)
        if not prof:
            raise HTTPException(status_code=404, detail="Professor not found")
        draft = repo.get_outreach_draft_by_professor(professor_id)
        threads = repo.get_threads_for_professor(professor_id)
        return {"professor": prof, "draft": draft, "threads": threads}


@api_router.post("/professors/{professor_id}/generate-draft")
def generate_draft_for_professor(professor_id: str):
    """Generate a personalized 4-paragraph email draft grounded in verified evidence."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        prof = repo.get_professor(professor_id)
        if not prof:
            raise HTTPException(status_code=404, detail="Professor not found")

        draft = personalization_engine.generate_draft(prof)
        repo.save_outreach_draft(draft)
        repo.update_professor_status(prof_id=professor_id, new_status="DRAFT_READY")
        return draft.to_dict()


# ------------------------------------------------------------------------------
# Scheduling & Time Blocks
# ------------------------------------------------------------------------------
@api_router.get("/schedule/blocks")
def get_schedule_blocks(target_date: Optional[str] = None):
    """Retrieve 13 regional time blocks with IST dispatch windows."""
    from datetime import date
    eval_date = date.fromisoformat(target_date) if target_date else date.today()

    with get_db() as conn:
        repo = OutreachRepository(conn)
        profs = repo.list_professors(limit=2000)
        summary = time_block_scheduler.generate_schedule_blocks_summary(profs, target_date=eval_date)
        return {"target_date": eval_date.isoformat(), "blocks": summary}


@api_router.get("/scheduler/missed-windows")
def get_missed_window_evaluations():
    """Scan upcoming queue items to evaluate expired morning windows and recovery status."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        profs = repo.list_professors(limit=100)
        evaluations = recovery_engine.recover_batch_queue(profs)
        return evaluations


# ------------------------------------------------------------------------------
# Human Review Inbox & Cryptographic Approval
# ------------------------------------------------------------------------------
@api_router.get("/replies")
def list_replies(status: Optional[str] = None):
    """List incoming reply drafts awaiting human review."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        reply_drafts = repo.list_reply_drafts(status)
        return {"count": len(reply_drafts), "reply_drafts": reply_drafts}


@api_router.get("/replies/{reply_draft_id}/hash")
def get_reply_content_hash(reply_draft_id: str):
    """Retrieve the exact SHA-256 cryptographic content hash for a reply draft."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        cursor = repo.conn.execute("SELECT * FROM reply_drafts WHERE reply_draft_id = ?;", (reply_draft_id,))
        draft = cursor.fetchone()
        if not draft:
            raise HTTPException(status_code=404, detail="Reply draft not found.")
        prof = repo.get_professor(draft["professor_id"])
        recipient = prof["email"] if prof else ""
        h = compute_draft_content_hash(
            draft_id=reply_draft_id,
            recipient=recipient,
            body_text=draft["draft_body"]
        )
        return {
            "reply_draft_id": reply_draft_id,
            "recipient": recipient,
            "content_hash": h,
            "word_count": draft["word_count"]
        }


@api_router.post("/replies/{reply_draft_id}/approve-and-send")
def human_approve_and_send_reply(
    reply_draft_id: str,
    req: HumanActionRequest,
    x_operator_session: Optional[str] = Header(None, alias="X-Operator-Session")
):
    """
    CRITICAL HUMAN-ONLY PERMISSION ACTION:
    Transmits an approved reply. Autonomous AI agents are strictly forbidden from this action.
    Requires server-side operator session and cryptographic hash verification.
    """
    token = req.session_token or x_operator_session
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            result = sending_gateway.human_send_reply(
                repo=repo,
                reply_draft_id=reply_draft_id,
                actor=SecurityRole.HUMAN_OPERATOR,
                session_token=token,
                approved_content_hash=req.approved_content_hash,
                operator_id=req.operator_id,
                dry_run=req.dry_run
            )
            return result
        except SecuritySessionError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
        except ApprovalTamperingError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except PermissionDeniedError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ------------------------------------------------------------------------------
# Follow-Up Queue & Cryptographic Approval
# ------------------------------------------------------------------------------
@api_router.get("/followups")
def list_followups(status: Optional[str] = None):
    """List follow-up drafts for human decision."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        drafts = repo.list_followup_drafts(status)
        return {"count": len(drafts), "followup_drafts": drafts}


@api_router.get("/followups/{followup_draft_id}/hash")
def get_followup_content_hash(followup_draft_id: str):
    """Retrieve the exact SHA-256 cryptographic content hash for a follow-up draft."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        cursor = repo.conn.execute("SELECT * FROM followup_drafts WHERE followup_draft_id = ?;", (followup_draft_id,))
        draft = cursor.fetchone()
        if not draft:
            raise HTTPException(status_code=404, detail="Followup draft not found.")
        prof = repo.get_professor(draft["professor_id"])
        recipient = prof["email"] if prof else ""
        h = compute_draft_content_hash(
            draft_id=followup_draft_id,
            recipient=recipient,
            body_text=draft["draft_body"]
        )
        return {
            "followup_draft_id": followup_draft_id,
            "recipient": recipient,
            "content_hash": h,
            "sequence_number": draft["sequence_number"]
        }


@api_router.post("/followups/{followup_draft_id}/approve-and-send")
def human_approve_and_send_followup(
    followup_draft_id: str,
    req: HumanActionRequest,
    x_operator_session: Optional[str] = Header(None, alias="X-Operator-Session")
):
    """
    CRITICAL HUMAN-ONLY PERMISSION ACTION:
    Transmits an approved follow-up email with session and content hash verification.
    """
    token = req.session_token or x_operator_session
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            result = sending_gateway.human_send_followup(
                repo=repo,
                followup_draft_id=followup_draft_id,
                actor=SecurityRole.HUMAN_OPERATOR,
                session_token=token,
                approved_content_hash=req.approved_content_hash,
                operator_id=req.operator_id,
                dry_run=req.dry_run
            )
            return result
        except SecuritySessionError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
        except ApprovalTamperingError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except PermissionDeniedError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ------------------------------------------------------------------------------
# Suppression Controls
# ------------------------------------------------------------------------------
@api_router.get("/suppressions")
def list_suppressions():
    """List all actively suppressed emails and domains."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        items = repo.list_suppressions()
        return {"count": len(items), "suppressions": items}


@api_router.post("/suppressions")
def add_suppression(req: ManualSuppressionRequest):
    """Manually add an email or domain to the permanent suppression list."""
    if not req.email and not req.domain:
        raise HTTPException(status_code=400, detail="Either email or domain must be specified.")
    with get_db() as conn:
        repo = OutreachRepository(conn)
        repo.add_suppression(
            email=req.email,
            domain=req.domain,
            reason=req.reason,
            notes=req.notes
        )
        return {"status": "SUCCESS", "message": "Suppression added successfully."}


# ------------------------------------------------------------------------------
# Master Workbook Sync & Forensic Audit
# ------------------------------------------------------------------------------
@api_router.post("/sync/import")
@api_router.post("/sync/import-workbook")
def import_from_workbook():
    """Import master workbook data into SQLite."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            stats = workbook_sync.import_to_database(repo)
            return {"status": "SUCCESS", "stats": stats}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/sync/audit-report")
def get_workbook_audit_report():
    """Retrieve comprehensive discrepancy and reconciliation audit between Excel and SQLite."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        audit_res = workbook_auditor.run_full_audit(repo)
        return audit_res


@api_router.post("/sync/export")
@api_router.post("/sync/write-back")
def sync_back_to_workbook():
    """Atomically write outreach statuses back to authoritative Excel workbook with backup."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            profs = repo.list_professors(limit=2000)
            summary_blocks = time_block_scheduler.generate_schedule_blocks_summary(profs)
            workbook_sync.sync_back_to_workbook(repo, summary_blocks)
            return {"status": "SUCCESS", "message": "Authoritative workbook synchronized with atomic backup."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------------------
# Personal Knowledge Base Management
# ------------------------------------------------------------------------------
@api_router.get("/knowledge-base/profile")
def get_research_profile():
    """Retrieve current student research profile and source documents audit."""
    return kb_manager.load_profile()


@api_router.put("/knowledge-base/profile")
def update_research_profile(profile_data: Dict[str, Any] = Body(...)):
    """Update editable student research profile."""
    updated = kb_manager.save_profile(profile_data)
    return {"status": "SUCCESS", "profile": updated}


@api_router.get("/knowledge-base/source-audit")
def get_source_document_audit():
    """Audit available vs missing source documents (such as approved CV)."""
    return kb_manager.audit_missing_sources()


# ------------------------------------------------------------------------------
# Diagnostics & Mail Connectivity
# ------------------------------------------------------------------------------
@api_router.get("/mail/diagnostic-imap")
def diagnostic_imap_inspect(mailbox: str = "INBOX"):
    """
    Execute read-only IMAP examination of IIT Delhi mailstore using RFC 3501 EXAMINE.
    Does not download message bodies or alter read flags.
    """
    return imap_client.diagnostic_imap_examine(mailbox)


@api_router.post("/diagnostics/test-mail")
def test_mail_connectivity():
    """Test IMAP and SMTP endpoints."""
    imap_res = imap_client.test_connection()
    smtp_res = smtp_client.test_connection()
    return {
        "imap_status": imap_res,
        "smtp_status": smtp_res,
        "dry_run_active": settings.DRY_RUN
    }


@api_router.post("/safety/emergency-stop")
def toggle_emergency_stop(active: bool = Body(..., embed=True), operator_id: str = Body(..., embed=True)):
    """Trigger or clear the global Emergency Stop killswitch."""
    settings.EMERGENCY_STOP = active
    audit_logger.log_event(
        event_type="EMERGENCY_STOP_TOGGLED",
        actor=SecurityRole.HUMAN_OPERATOR.value,
        operator_id=operator_id,
        entity_type="SYSTEM_SAFETY",
        entity_id="KILLSWITCH",
        details={"active": active}
    )
    return {"emergency_stop": settings.EMERGENCY_STOP, "operator_id": operator_id}


@api_router.get("/audit-logs")
def get_audit_logs(limit: int = 50):
    """Retrieve recent immutable audit events."""
    events = []
    log_file = settings.LOGS_DIR / "audit_events.jsonl"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            import json
            for line in reversed(lines[-limit:]):
                try:
                    events.append(json.loads(line.strip()))
                except Exception:
                    pass
    return {"count": len(events), "events": events}
