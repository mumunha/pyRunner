"""PyRunner Dashboard - FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard.config import PYRUNNER_ROOT, get_logs_dir, get_projects_dir, get_supervisor_conf_dir, load_config
from dashboard.database import init_db
from dashboard.git_poller import GitPoller
from dashboard.scheduler_service import SchedulerService
from dashboard.utils import format_duration, short_commit, status_bg, status_color
from dashboard.routers import deploys, projects, schedules, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("pyrunner")

# Global service instances
git_poller: GitPoller | None = None
scheduler_service: SchedulerService | None = None

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global git_poller, scheduler_service

    # Ensure directories exist
    for d in [
        PYRUNNER_ROOT,
        get_projects_dir(),
        get_logs_dir(),
        get_supervisor_conf_dir(),
        PYRUNNER_ROOT / "data",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # Initialize DB
    init_db()

    cfg = load_config()

    # Start scheduler
    scheduler_service = SchedulerService(cfg)
    scheduler_service.start()
    app.state.scheduler = scheduler_service

    # Start git poller
    git_poller = GitPoller(cfg, scheduler_service)
    git_poller.start()
    app.state.git_poller = git_poller

    logger.info("PyRunner started on port %s", cfg["server"]["port"])
    yield

    # Shutdown
    if git_poller:
        git_poller.stop()
    if scheduler_service:
        scheduler_service.stop()
    logger.info("PyRunner stopped")


app = FastAPI(title="PyRunner", version="1.0.0", lifespan=lifespan)

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["short_commit"] = short_commit
templates.env.filters["format_duration"] = format_duration
templates.env.filters["status_color"] = status_color
templates.env.filters["status_bg"] = status_bg

# Static files (create dir if needed)
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include routers
app.include_router(projects.router, prefix="/projects", tags=["projects"])
app.include_router(schedules.router, prefix="/schedules", tags=["schedules"])
app.include_router(deploys.router, prefix="/deploys", tags=["deploys"])
app.include_router(settings.router, prefix="/settings", tags=["settings"])

# Expose templates and services to routers via app state
app.state.templates = templates


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    from dashboard.database import ActivityEvent, Deploy, Execution, Project, get_session_factory
    from sqlalchemy import desc

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        total = db.query(Project).count()
        running = db.query(Project).filter(Project.status == "running").count()
        stopped = db.query(Project).filter(Project.status == "stopped").count()
        errored = db.query(Project).filter(Project.status == "error").count()
        scheduled_count = db.query(Project).filter(Project.type == "scheduled").count()

        recent_activity = (
            db.query(ActivityEvent)
            .order_by(desc(ActivityEvent.created_at))
            .limit(20)
            .all()
        )

        cfg = load_config()
        supervisor_ok = _check_supervisor()
        poller_ok = git_poller is not None and git_poller.running

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "stats": {
                    "total": total,
                    "running": running,
                    "stopped": stopped,
                    "errored": errored,
                    "scheduled": scheduled_count,
                },
                "recent_activity": recent_activity,
                "supervisor_ok": supervisor_ok,
                "poller_ok": poller_ok,
                "poll_interval": cfg["git"]["poll_interval_minutes"],
            },
        )
    finally:
        db.close()


@app.get("/api/dashboard/stats")
def api_stats(request: Request):
    from dashboard.database import Project, get_session_factory
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        return {
            "total": db.query(Project).count(),
            "running": db.query(Project).filter(Project.status == "running").count(),
            "stopped": db.query(Project).filter(Project.status == "stopped").count(),
            "errored": db.query(Project).filter(Project.status == "error").count(),
        }
    finally:
        db.close()


def _check_supervisor() -> bool:
    """Quick check if Supervisord is reachable."""
    try:
        from dashboard.supervisor_client import SupervisorClient
        cfg = load_config()
        client = SupervisorClient(cfg)
        client.get_state()
        return True
    except Exception:
        return False


def start():
    cfg = load_config()
    uvicorn.run(
        "dashboard.main:app",
        host=cfg["server"]["host"],
        port=cfg["server"]["port"],
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    start()
