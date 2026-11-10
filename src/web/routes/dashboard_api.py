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
from typing import AsyncGenerator
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
