"""Project management routes."""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from dashboard.config import get_logs_dir, get_projects_dir, get_supervisor_conf_dir, load_config
from dashboard.database import ActivityEvent, Deploy, Execution, Project, Schedule, get_db
from dashboard.git_poller import write_supervisor_conf
from dashboard.supervisor_client import SupervisorClient
from dashboard.utils import strip_ansi, tail_file

router = APIRouter()


def get_templates(request: Request):
    return request.app.state.templates


def get_git_poller(request: Request):
    return request.app.state.git_poller


def get_scheduler(request: Request):
    return request.app.state.scheduler


def _slug(url: str) -> str:
    """Derive a project name from a git URL."""
    name = url.rstrip("/").rstrip(".git").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name).lower().strip("-")
    return name or "project"


@router.get("/", response_class=HTMLResponse)
def list_projects(request: Request, status: str = "", type: str = "", db: Session = Depends(get_db)):
    templates = get_templates(request)
    query = db.query(Project)
    if status:
        query = query.filter(Project.status == status)
    if type:
        query = query.filter(Project.type == type)
    projects = query.order_by(Project.name).all()
    return templates.TemplateResponse(
        "projects.html",
        {"request": request, "projects": projects, "filter_status": status, "filter_type": type},
    )


@router.post("/add")
def add_project(
    request: Request,
    repo_url: str = Form(...),
    branch: str = Form("main"),
    db: Session = Depends(get_db),
):
    name = _slug(repo_url)
    # Ensure unique name
    base = name
    counter = 1
    while db.query(Project).filter(Project.name == name).first():
        name = f"{base}-{counter}"
        counter += 1

    project = Project(
        name=name,
        repo_url=repo_url,
        branch=branch,
        status="registered",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    db.add(ActivityEvent(
        event_type="deploy",
        project_name=name,
        message=f"Project {name} registered from {repo_url}",
        level="info",
    ))
    db.commit()

    # Trigger clone asynchronously
    git_poller = get_git_poller(request)
    if git_poller:
        git_poller.clone_project(project.id)

    return RedirectResponse(url="/projects", status_code=303)


@router.get("/{name}", response_class=HTMLResponse)
def project_detail(request: Request, name: str, db: Session = Depends(get_db)):
    templates = get_templates(request)
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Supervisor info
    cfg = load_config()
    sup_info = None
    try:
        client = SupervisorClient(cfg)
        sup_info = client.get_process_info(name)
    except Exception:
        pass

    deploys = (
        db.query(Deploy)
        .filter(Deploy.project_id == project.id)
        .order_by(Deploy.created_at.desc())
        .limit(20)
        .all()
    )
    schedules = db.query(Schedule).filter(Schedule.project_id == project.id).all()
    executions = (
        db.query(Execution)
        .filter(Execution.project_id == project.id)
        .order_by(Execution.created_at.desc())
        .limit(20)
        .all()
    )

    # Scheduler - get next run times
    scheduler = get_scheduler(request)
    next_runs = {}
    for s in schedules:
        nr = scheduler.get_next_run(s.id) if scheduler else None
        next_runs[s.id] = nr

    return templates.TemplateResponse(
        "project_detail.html",
        {
            "request": request,
            "project": project,
            "sup_info": sup_info,
            "deploys": deploys,
            "schedules": schedules,
            "executions": executions,
            "next_runs": next_runs,
        },
    )


@router.post("/{name}/start")
def start_project(request: Request, name: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)
    cfg = load_config()
    client = SupervisorClient(cfg)
    ok = client.start_process(name)
    if ok:
        project.status = "running"
        db.add(ActivityEvent(event_type="status_change", project_name=name, message=f"Started {name}", level="success"))
        db.commit()
    return RedirectResponse(url=f"/projects/{name}", status_code=303)


@router.post("/{name}/stop")
def stop_project(request: Request, name: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)
    cfg = load_config()
    client = SupervisorClient(cfg)
    ok = client.stop_process(name)
    if ok:
        project.status = "stopped"
        db.add(ActivityEvent(event_type="status_change", project_name=name, message=f"Stopped {name}", level="info"))
        db.commit()
    return RedirectResponse(url=f"/projects/{name}", status_code=303)


@router.post("/{name}/restart")
def restart_project(request: Request, name: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)
    cfg = load_config()
    client = SupervisorClient(cfg)
    ok = client.restart_process(name)
    if ok:
        project.status = "running"
        db.add(ActivityEvent(event_type="status_change", project_name=name, message=f"Restarted {name}", level="success"))
        db.commit()
    return RedirectResponse(url=f"/projects/{name}", status_code=303)


@router.post("/{name}/deploy")
def force_deploy(request: Request, name: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)
    git_poller = get_git_poller(request)
    if git_poller:
        git_poller.trigger_deploy(project.id)
    return RedirectResponse(url=f"/projects/{name}", status_code=303)


@router.post("/{name}/delete")
def delete_project(request: Request, name: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)

    # Stop process first
    cfg = load_config()
    try:
        client = SupervisorClient(cfg)
        client.stop_process(name)
        client.remove_process_group(name)
    except Exception:
        pass

    # Remove supervisor conf
    conf_path = get_supervisor_conf_dir() / f"{name}.conf"
    if conf_path.exists():
        conf_path.unlink()
    subprocess.run(["supervisorctl", "reread"], capture_output=True)
    subprocess.run(["supervisorctl", "update"], capture_output=True)

    db.delete(project)
    db.commit()
    return RedirectResponse(url="/projects", status_code=303)


@router.get("/{name}/logs", response_class=HTMLResponse)
def view_logs(request: Request, name: str, lines: int = 200, db: Session = Depends(get_db)):
    templates = get_templates(request)
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)

    log_dir = get_logs_dir()
    stdout_lines = tail_file(log_dir / f"{name}.stdout.log", lines)
    stderr_lines = tail_file(log_dir / f"{name}.stderr.log", lines)

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "project": project,
            "stdout_lines": stdout_lines,
            "stderr_lines": stderr_lines,
        },
    )


@router.get("/api/{name}/logs/tail", response_class=HTMLResponse)
def tail_logs_api(name: str, limit: int = 50):
    """HTMX polling endpoint for live logs — returns HTML lines."""
    log_dir = get_logs_dir()
    lines = tail_file(log_dir / f"{name}.stdout.log", limit)
    if not lines:
        return HTMLResponse('<span class="text-gray-600">No output yet.</span>')
    from html import escape
    html = "".join(f'<div class="hover:bg-gray-800/50">{escape(line)}</div>' for line in lines)
    return HTMLResponse(html)


@router.get("/api/{name}/status")
def status_api(name: str, db: Session = Depends(get_db)):
    """Quick status check for HTMX polling."""
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        return {"status": "unknown"}
    cfg = load_config()
    try:
        client = SupervisorClient(cfg)
        info = client.get_process_info(name)
        if info:
            statename = info.get("statename", "").lower()
            if statename in ("running",):
                project.status = "running"
                db.commit()
            elif statename in ("stopped", "exited"):
                if project.status == "running":
                    project.status = "stopped"
                    db.commit()
    except Exception:
        pass
    return {"status": project.status}
