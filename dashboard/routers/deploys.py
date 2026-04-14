"""Deploy log routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from dashboard.database import Deploy, Project, get_db

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def deploy_log(
    request: Request,
    project: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    templates = request.app.state.templates

    query = db.query(Deploy).join(Project)
    if project:
        query = query.filter(Project.name == project)
    if status:
        query = query.filter(Deploy.status == status)

    deploys = query.order_by(desc(Deploy.created_at)).limit(200).all()
    projects = db.query(Project).order_by(Project.name).all()

    return templates.TemplateResponse(
        "deploy_log.html",
        {
            "request": request,
            "deploys": deploys,
            "projects": projects,
            "filter_project": project,
            "filter_status": status,
        },
    )
