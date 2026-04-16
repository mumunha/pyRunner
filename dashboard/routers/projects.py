"""Project management routes."""
from __future__ import annotations

import os
import re
import subprocess
import threading
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


def _list_py_files(project_name: str) -> list[str]:
    """Return .py filenames at the project root, excluding hidden/venv dirs."""
    project_dir = get_projects_dir() / project_name
    if not project_dir.exists():
        return []
    skip = {".venv", "venv", "__pycache__", ".git", "node_modules"}
    return sorted(
        f.name
        for f in project_dir.iterdir()
        if f.is_file() and f.suffix == ".py" and f.parent.name not in skip
    )


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
        request,
        "projects.html",
        {"projects": projects, "filter_status": status, "filter_type": type},
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

    py_files = _list_py_files(name)

    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "project": project,
            "sup_info": sup_info,
            "deploys": deploys,
            "schedules": schedules,
            "executions": executions,
            "next_runs": next_runs,
            "py_files": py_files,
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


@router.post("/{name}/run-script")
def run_script(
    request: Request,
    name: str,
    script: str = Form(...),
    db: Session = Depends(get_db),
):
    """Run a .py file from the project directory in a background thread."""
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)

    # Security: reject any path traversal — allow only a bare filename
    script_name = Path(script).name
    if script_name != script or not script_name.endswith(".py"):
        raise HTTPException(status_code=400, detail="Invalid script name")

    project_dir = get_projects_dir() / name
    if not (project_dir / script_name).exists():
        raise HTTPException(status_code=404, detail="Script not found")

    # Create execution record immediately so it shows in the UI
    execution = Execution(
        project_id=project.id,
        trigger_time=datetime.utcnow(),
        status="running",
    )
    db.add(execution)
    db.add(ActivityEvent(
        event_type="execution",
        project_name=name,
        message=f"Manual run started: {script_name}",
        level="info",
    ))
    db.commit()
    db.refresh(execution)
    exec_id = execution.id

    def _run_in_background():
        from dashboard.database import get_session_factory
        SessionLocal = get_session_factory()
        session = SessionLocal()
        try:
            start = datetime.utcnow()
            log_path = get_logs_dir() / f"{name}.{script_name}.log"

            # Simple .env loader (no extra dependency)
            env_vars = dict(os.environ)
            env_file = project_dir / (project.env_file or ".env")
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")

            with open(log_path, "a") as logf:
                logf.write(f"\n--- Manual run: {script_name}  {start.isoformat()} ---\n")
                result = subprocess.run(
                    ["uv", "run", "python", script_name],
                    cwd=project_dir,
                    env=env_vars,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    timeout=3600,
                )

            end = datetime.utcnow()
            duration = (end - start).total_seconds()
            success = result.returncode == 0

            exec_rec = session.query(Execution).filter(Execution.id == exec_id).first()
            if exec_rec:
                exec_rec.start_time = start
                exec_rec.end_time = end
                exec_rec.duration_seconds = duration
                exec_rec.exit_code = result.returncode
                exec_rec.status = "success" if success else "failed"
                exec_rec.log_path = str(log_path)
                session.commit()

            session.add(ActivityEvent(
                event_type="execution",
                project_name=name,
                message=f"Manual run {script_name} {'succeeded' if success else 'failed'} (exit {result.returncode})",
                level="success" if success else "error",
            ))
            session.commit()
        except subprocess.TimeoutExpired:
            exec_rec = session.query(Execution).filter(Execution.id == exec_id).first()
            if exec_rec:
                exec_rec.status = "timeout"
                session.commit()
        except Exception as exc:
            exec_rec = session.query(Execution).filter(Execution.id == exec_id).first()
            if exec_rec:
                exec_rec.status = "failed"
                session.commit()
            session.add(ActivityEvent(
                event_type="execution",
                project_name=name,
                message=f"Manual run {script_name} error: {exc}",
                level="error",
            ))
            session.commit()
        finally:
            session.close()

    threading.Thread(target=_run_in_background, daemon=True).start()
    return RedirectResponse(url=f"/projects/{name}", status_code=303)


@router.post("/{name}/delete")
def delete_project(
    request: Request,
    name: str,
    delete_files: str = Form("off"),
    db: Session = Depends(get_db),
):
    import shutil

    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)

    # Stop process and remove from Supervisord
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

    # Optionally delete project files from disk
    if delete_files == "on":
        project_dir = get_projects_dir() / name
        if project_dir.exists():
            shutil.rmtree(project_dir)

    db.delete(project)
    db.commit()
    return RedirectResponse(url="/projects", status_code=303)


@router.get("/{name}/executions/{exec_id}/log", response_class=HTMLResponse)
def view_execution_log(
    request: Request,
    name: str,
    exec_id: int,
    db: Session = Depends(get_db),
):
    """Show the stdout/stderr captured for a manual script run."""
    templates = get_templates(request)
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        raise HTTPException(status_code=404)
    from dashboard.database import Execution as ExecModel
    execution = db.query(ExecModel).filter(
        ExecModel.id == exec_id, ExecModel.project_id == project.id
    ).first()
    if not execution:
        raise HTTPException(status_code=404)

    lines: list[str] = []
    if execution.log_path:
        lines = tail_file(execution.log_path, 2000)

    return templates.TemplateResponse(
        request,
        "execution_log.html",
        {
            "project": project,
            "execution": execution,
            "lines": lines,
        },
    )


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
        request,
        "logs.html",
        {
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
