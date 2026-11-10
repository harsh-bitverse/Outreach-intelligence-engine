
from fastapi import UploadFile, File, Body, Form, HTTPException
from src.mail.bounce_recovery import bounce_recovery_service
from src.opportunities.portal_manager import portal_manager_service
"""
FastAPI Dashboard Router for Outreach Intelligence Engine.
Provides read-only operational endpoints and SSE streaming for real-time observability:
- /api/dashboard/overview
- /api/dashboard/workers
- /api/dashboard/discovery
- /api/dashboard/outreach
- /api/dashboard/portals
- /api/dashboard/outcomes
- /api/dashboard/manual-actions
- /api/dashboard/excel-sync
- /api/dashboard/alerts
- /api/dashboard/events/stream (SSE)
"""

import asyncio
import json
from typing import AsyncGenerator, Dict, Any, List, Optional
from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from src.web.routes.dashboard_service import dashboard_service


dashboard_router = APIRouter(prefix="/api/dashboard", tags=["Operations Dashboard"])


@dashboard_router.get("/overview")
def get_overview():
    """Retrieve high-level operational KPIs across all subsystems."""
    return dashboard_service.get_overview()


@dashboard_router.get("/workers")
def get_workers():
    """Retrieve durable health states and heartbeats for all 5 workers."""
    return dashboard_service.get_worker_health()


@dashboard_router.get("/discovery")
def get_discovery():
    """Retrieve discovery runs, checkpoints, QS coverage, and 5-stage sync pipeline."""
    return dashboard_service.get_discovery_details()


@dashboard_router.get("/outreach")
def get_outreach():
    """Retrieve outreach metrics by category, active regional blocks, and queue."""
    return dashboard_service.get_outreach_details()


@dashboard_router.get("/portals")
def get_portals():
    """Retrieve 269 portal applications breakdown, mock vs live status, and runs."""
    return dashboard_service.get_portal_details()


@dashboard_router.get("/outcomes")
def get_outcomes():
    """Retrieve positive engagement queue, 14-intent classifications, and DSN bounces."""
    return dashboard_service.get_outcomes_details()


@dashboard_router.get("/manual-actions")
def get_manual_actions():
    """Retrieve prioritized operator manual action tasks (CERN, DAAD, Mitacs)."""
    return dashboard_service.get_manual_actions()


@dashboard_router.get("/excel-sync")
def get_excel_sync():
    """Retrieve 18-sheet parity against SQLite and workbook file health."""
    return dashboard_service.get_excel_sync_status()


@dashboard_router.get("/alerts")
def get_alerts():
    """Retrieve active evidence-driven operational alerts."""
    return dashboard_service.get_active_alerts()


async def event_generator(request: Request) -> AsyncGenerator[str, None]:
    """Stream real-time operational snapshots every 5 seconds via Server-Sent Events."""
    while True:
        if await request.is_disconnected():
            break
        try:
            overview = dashboard_service.get_overview()
            workers = dashboard_service.get_worker_health()
            data = {
                "current_time_ist": overview.get("current_time_ist"),
                "kpis": overview.get("kpis"),
                "workers": workers.get("workers"),
                "alerts": overview.get("alerts", [])
            }
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        await asyncio.sleep(5)


@dashboard_router.get("/events/stream")
async def stream_events(request: Request):
    """Native Server-Sent Events endpoint for low-latency live operations streaming."""
    return StreamingResponse(
        event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )



# --------------------------------------------------------------------------
# Evidence & Provenance Endpoints (Phase 7)
# --------------------------------------------------------------------------
@dashboard_router.get("/evidence/{record_type}/{record_id:path}")
def get_evidence(record_type: str, record_id: str):
    """
    Retrieve structured verification evidence, provenance, and original citations.
    Returns HTTP 200 with status SOURCE_NOT_AVAILABLE fallback if unarchived, never throws 404.
    """
    return dashboard_service.get_evidence(record_type, record_id)


# --------------------------------------------------------------------------
# Bounce Recovery Endpoints (Phase 4)
# --------------------------------------------------------------------------
@dashboard_router.get("/bounces/recovery")
def get_bounce_recovery():
    """Retrieve categorized bounce diagnostics, suppression status, and verified replacement proposals."""
    return {
        "recovery_records": bounce_recovery_service.list_recovery_records(),
        "total_tracked": len(bounce_recovery_service.list_recovery_records())
    }


@dashboard_router.post("/bounces/{replacement_id}/authorize")
def authorize_bounce_replacement(replacement_id: int):
    """Authorizes verified replacement email for outreach dispatch and suppresses old address."""
    try:
        return bounce_recovery_service.authorize_replacement(replacement_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------
# Real Portal Application Endpoints (Phase 5)
# --------------------------------------------------------------------------
@dashboard_router.get("/portals/{opp_id:path}")
def get_portal_application(opp_id: str):
    """Retrieve full application detail, form fields, mapped vault documents, and preparation status."""
    try:
        return portal_manager_service.get_application_detail(opp_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@dashboard_router.post("/portals/{opp_id:path}/additional-info")
def save_portal_additional_info(opp_id: str, payload: Dict[str, Any] = Body(...)):
    """Save applicant additional information and tailored instructions for an application."""
    text_info = payload.get("additional_information", "")
    return portal_manager_service.save_additional_information(opp_id, text_info)


@dashboard_router.post("/portals/{opp_id:path}/upload")
async def upload_portal_document(opp_id: str, file: UploadFile = File(...)):
    """Upload document securely into application's private vault (data/vault/applications/<opp_id>/)."""
    content = await file.read()
    return portal_manager_service.upload_vault_document(opp_id, file.filename, content)


@dashboard_router.post("/portals/{opp_id:path}/prepare-tailored")
def prepare_portal_tailored(opp_id: str):
    """Prepare tailored statement of purpose and responses grounded in verified applicant profile."""
    return portal_manager_service.prepare_tailored_answers(opp_id)


@dashboard_router.post("/portals/{opp_id:path}/authorize-submission")
def authorize_portal_submission(opp_id: str, payload: Dict[str, Any] = Body(...)):
    """Authorize or revoke submission authorization for an application."""
    authorized = payload.get("authorized", True)
    return portal_manager_service.authorize_submission(opp_id, authorized)


@dashboard_router.post("/portals/{opp_id:path}/submit")
def submit_portal_application(opp_id: str):
    """Submit authorized application on supported portal, capturing genuine receipt."""
    try:
        return portal_manager_service.submit_application(opp_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
