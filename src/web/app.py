"""
FastAPI Web Application Server for Outreach Intelligence Engine.
Serves both the REST API and the operator dashboard UI.
"""

import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response
from config.settings import settings
from src.database.connection import initialize_database, get_db
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.web.routes.api import api_router
from src.web.routes.dashboard_api import dashboard_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle startup and shutdown handler."""
    # Initialize SQLite schema and seed initial suppressions
    initialize_database()

    # Automatically import authoritative Excel database if SQLite is empty
    with get_db() as conn:
        repo = OutreachRepository(conn)
        if repo.count_professors() == 0 and settings.MASTER_WORKBOOK_PATH.exists():
            try:
                sync = DynamicWorkbookSync()
                sync.import_to_database(repo)
            except Exception as e:
                print(f"[Warning] Auto-import of master workbook failed on startup: {e}")

    yield


app = FastAPI(
    title="International Research Outreach Intelligence Engine",
    description="Local-first research outreach, scheduling, and reply intelligence platform for IIT Delhi undergraduate",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(api_router)
app.include_router(dashboard_router)


@app.middleware("http")
async def add_strict_cache_control_headers(request, call_next):
    """Enforce strict no-cache headers across HTML, static assets, and API routes."""
    response = await call_next(request)
    if (
        request.url.path.startswith("/static")
        or request.url.path in ("/", "/favicon.ico")
        or request.url.path.startswith("/api/dashboard")
    ):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Mount Static UI Files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico")
def serve_favicon():
    """Serve favicon or 204 to suppress browser console 404s."""
    favicon = static_dir / "favicon.ico"
    if favicon.exists():
        return FileResponse(
            str(favicon),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    return Response(status_code=204)


@app.get("/")
def serve_dashboard():
    """Serve the main operator dashboard SPA with dynamic asset cache-busting."""
    index_file = static_dir / "index.html"
    if not index_file.exists():
        return {"message": "Outreach Intelligence Engine API is online. UI assets loading."}

    html_content = index_file.read_text(encoding="utf-8")

    # Compute content hashes for dynamic asset versioning
    app_js_path = static_dir / "app.js"
    style_css_path = static_dir / "style.css"

    js_hash = hashlib.sha256(app_js_path.read_bytes()).hexdigest()[:10] if app_js_path.exists() else str(int(time.time()))
    css_hash = hashlib.sha256(style_css_path.read_bytes()).hexdigest()[:10] if style_css_path.exists() else str(int(time.time()))

    # Inject dynamic cache-busting version query parameters
    html_content = html_content.replace('href="/static/style.css"', f'href="/static/style.css?v={css_hash}"')
    html_content = html_content.replace('src="/static/app.js"', f'src="/static/app.js?v={js_hash}"')

    return HTMLResponse(
        content=html_content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
