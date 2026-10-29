"""
FastAPI Web Application Server for Outreach Intelligence Engine.
Serves both the REST API and the operator dashboard UI.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config.settings import settings
from src.database.connection import initialize_database, get_db
from src.database.repository import OutreachRepository
from src.database.workbook_sync import DynamicWorkbookSync
from src.web.routes.api import api_router


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

# Mount API Router
app.include_router(api_router)

# Mount Static UI Files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_dashboard():
    """Serve the main operator dashboard SPA."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Outreach Intelligence Engine API is online. UI assets loading."}
