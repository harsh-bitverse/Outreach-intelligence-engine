"""
FastAPI REST API Routes for Outreach Intelligence Engine.
Provides clean endpoints for dashboard metrics, professor management,
IST scheduling, human review inbox, suppressions, and sync controls.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Body, Header
from pydantic import BaseModel, Field
from config.settings import settings
from src.database.connection import get_db
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.intelligence.personalization import PersonalizationEngine
from src.intelligence.reply_classifier import reply_classifier
from src.intelligence.reply_drafter import reply_drafter
from src.scheduler.time_blocks import time_block_scheduler
from src.scheduler.timezone_engine import timezone_engine
from src.mail.imap_client import imap_client
from src.mail.smtp_client import smtp_client
from src.mail.sending_gateway import sending_gateway
from src.security.permissions import SecurityRole, SecurityAction, PermissionDeniedError
from src.security.audit_logger import audit_logger


api_router = APIRouter(prefix="/api")
personalization_engine = PersonalizationEngine()
workbook_sync = DynamicWorkbookSync()


# ------------------------------------------------------------------------------
# Request Schemas
# ------------------------------------------------------------------------------
class HumanActionRequest(BaseModel):
    operator_id: str = Field(..., description="Verified Human Operator Identifier (e.g. 'Harshith N')")
    dry_run: bool = Field(True, description="When true, executes dry-run simulation without SMTP wire transmission")


class ManualSuppressionRequest(BaseModel):
    email: Optional[str] = None
    domain: Optional[str] = None
    reason: str = "MANUAL"
    notes: str = ""


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
        is_supp, supp_reason = repo.is_suppressed(prof.get("email"))
        return {
            "professor": prof,
            "draft": draft,
            "is_suppressed": is_supp,
            "suppression_reason": supp_reason
        }


@api_router.post("/professors/{professor_id}/generate-draft")
def generate_draft_for_professor(professor_id: str):
    """Generate a personalized four-paragraph outreach draft."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        prof = repo.get_professor(professor_id)
        if not prof:
            raise HTTPException(status_code=404, detail="Professor not found")

        draft = personalization_engine.generate_draft(prof)
        is_valid, error = personalization_engine.validate_draft(draft)
        if not is_valid:
            raise HTTPException(status_code=422, detail=error)

        repo.save_outreach_draft(draft)
        repo.update_professor_status(professor_id, "DRAFT_READY")
        return {"draft": draft, "valid": True}


@api_router.post("/professors/{professor_id}/approve-draft")
def approve_draft_for_professor(professor_id: str, operator_id: str = Body(..., embed=True)):
    """Operator approval of a first-contact draft for queued dispatch."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        draft = repo.get_outreach_draft_by_professor(professor_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")

        repo.conn.execute("""
            UPDATE outreach_drafts
            SET status = 'APPROVED', approved_at = datetime('now'), approved_by = ?
            WHERE draft_id = ?;
        """, (operator_id, draft["draft_id"]))
        repo.update_professor_status(professor_id, "APPROVED")

        audit_logger.log_event(
            event_type="OUTREACH_DRAFT_APPROVED",
            actor=SecurityRole.HUMAN_OPERATOR.value,
            operator_id=operator_id,
            entity_type="OUTREACH_DRAFT",
            entity_id=draft["draft_id"],
            details={"professor_id": professor_id}
        )
        return {"status": "APPROVED", "draft_id": draft["draft_id"]}


# ------------------------------------------------------------------------------
# IST Scheduler & Time Blocks
# ------------------------------------------------------------------------------
@api_router.get("/scheduler/blocks")
def get_scheduler_blocks():
    """Retrieve regional schedule blocks and IST dispatch times."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        profs = repo.list_professors(limit=1500)
        blocks = time_block_scheduler.generate_schedule_blocks_summary(profs)
        return {
            "operator_timezone": settings.OPERATOR_TIMEZONE,
            "target_arrival_window": "8:00 AM - 9:00 AM Destination-Local Time",
            "blocks": blocks
        }


# ------------------------------------------------------------------------------
# Reply Intelligence & Human Review Inbox
# ------------------------------------------------------------------------------
@api_router.get("/replies")
def list_replies(status: Optional[str] = None):
    """List incoming reply drafts awaiting human review."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        reply_drafts = repo.list_reply_drafts(status)
        return {"count": len(reply_drafts), "reply_drafts": reply_drafts}


@api_router.post("/replies/{reply_draft_id}/approve-and-send")
def human_approve_and_send_reply(reply_draft_id: str, req: HumanActionRequest):
    """
    CRITICAL HUMAN-ONLY PERMISSION ACTION:
    Transmits an approved reply. Autonomous AI agents are strictly forbidden from this action.
    """
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            result = sending_gateway.human_send_reply(
                repo=repo,
                reply_draft_id=reply_draft_id,
                actor=SecurityRole.HUMAN_OPERATOR,
                operator_id=req.operator_id,
                dry_run=req.dry_run
            )
            return result
        except PermissionDeniedError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------------------
# Follow-Up Queue
# ------------------------------------------------------------------------------
@api_router.get("/followups")
def list_followups(status: Optional[str] = None):
    """List follow-up drafts for human decision."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        drafts = repo.list_followup_drafts(status)
        return {"count": len(drafts), "followup_drafts": drafts}


@api_router.post("/followups/{followup_draft_id}/approve-and-send")
def human_approve_and_send_followup(followup_draft_id: str, req: HumanActionRequest):
    """
    CRITICAL HUMAN-ONLY PERMISSION ACTION:
    Transmits an approved follow-up email.
    """
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            result = sending_gateway.human_send_followup(
                repo=repo,
                followup_draft_id=followup_draft_id,
                actor=SecurityRole.HUMAN_OPERATOR,
                operator_id=req.operator_id,
                dry_run=req.dry_run
            )
            return result
        except PermissionDeniedError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


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
    with get_db() as conn:
        repo = OutreachRepository(conn)
        supp_id = repo.add_suppression(
            email=req.email,
            domain=req.domain,
            reason=req.reason,
            notes=req.notes
        )
        audit_logger.log_event(
            event_type="SUPPRESSION_ADDED_MANUAL",
            actor=SecurityRole.HUMAN_OPERATOR.value,
            entity_type="SUPPRESSION",
            entity_id=supp_id,
            details={"email": req.email, "domain": req.domain, "reason": req.reason}
        )
        return {"status": "SUCCESS", "suppression_id": supp_id}


# ------------------------------------------------------------------------------
# Master Workbook Synchronization
# ------------------------------------------------------------------------------
@api_router.post("/sync/import-workbook")
def import_workbook():
    """Import authoritative Excel workbook into SQLite dynamically."""
    with get_db() as conn:
        repo = OutreachRepository(conn)
        try:
            stats = workbook_sync.import_to_database(repo)
            return {"status": "SUCCESS", "stats": stats}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/sync/write-back")
def sync_back_to_workbook():
    """Write updated outreach statuses and time blocks back to Excel atomically."""
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
# Diagnostics & Safety Controls
# ------------------------------------------------------------------------------
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
