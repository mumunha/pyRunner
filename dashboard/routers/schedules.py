"""Schedule management routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from dashboard.database import ActivityEvent, Execution, Project, Schedule, get_db

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _scheduler(request: Request):
    return request.app.state.scheduler


@router.get("/", response_class=HTMLResponse)
def list_schedules(request: Request, db: Session = Depends(get_db)):
    templates = _templates(request)
    schedules = (
        db.query(Schedule)
        .join(Project)
        .order_by(Project.name)
        .all()
    )
    scheduler = _scheduler(request)
    next_runs = {}
    for s in schedules:
        nr = scheduler.get_next_run(s.id) if scheduler else None
        next_runs[s.id] = nr

    recent_executions = (
        db.query(Execution)
        .order_by(desc(Execution.created_at))
        .limit(30)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "schedules.html",
        {
            "schedules": schedules,
            "next_runs": next_runs,
            "recent_executions": recent_executions,
        },
    )


@router.post("/add")
def add_schedule(
    request: Request,
    project_id: int = Form(...),
    cron_expression: str = Form(...),
    timeout_seconds: int = Form(3600),
    entrypoint: str = Form(""),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    schedule = Schedule(
        project_id=project_id,
        cron_expression=cron_expression,
        entrypoint=entrypoint.strip() or None,
        timeout_seconds=timeout_seconds,
        enabled=True,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    scheduler = _scheduler(request)
    if scheduler:
        scheduler.add_schedule(schedule.id, project_id, cron_expression, timeout_seconds)

    db.add(ActivityEvent(
        event_type="schedule",
        project_name=project.name,
        message=f"Schedule created for {project.name}: {cron_expression}",
        level="info",
    ))
    db.commit()

    # Redirect back - check referer or go to project detail
    return RedirectResponse(url=f"/projects/{project.name}", status_code=303)


@router.post("/{schedule_id}/delete")
def delete_schedule(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404)

    project_name = schedule.project.name if schedule.project else "unknown"
    scheduler = _scheduler(request)
    if scheduler:
        scheduler.remove_schedule(schedule_id)

    db.delete(schedule)
    db.commit()
    return RedirectResponse(url=f"/projects/{project_name}", status_code=303)


@router.post("/{schedule_id}/pause")
def pause_schedule(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404)

    schedule.enabled = False
    db.commit()

    scheduler = _scheduler(request)
    if scheduler:
        scheduler.pause_schedule(schedule_id)

    project_name = schedule.project.name if schedule.project else "unknown"
    return RedirectResponse(url=f"/projects/{project_name}", status_code=303)


@router.post("/{schedule_id}/resume")
def resume_schedule(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404)

    schedule.enabled = True
    db.commit()

    scheduler = _scheduler(request)
    if scheduler:
        scheduler.resume_schedule(schedule_id)

    project_name = schedule.project.name if schedule.project else "unknown"
    return RedirectResponse(url=f"/projects/{project_name}", status_code=303)


@router.post("/{schedule_id}/run-now")
def run_now(request: Request, schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404)

    scheduler = _scheduler(request)
    if scheduler:
        scheduler.run_now(schedule_id, schedule.project_id, schedule.timeout_seconds)

    project_name = schedule.project.name if schedule.project else "unknown"
    db.add(ActivityEvent(
        event_type="execution",
        project_name=project_name,
        message=f"Manual run triggered for {project_name}",
        level="info",
    ))
    db.commit()
    return RedirectResponse(url=f"/projects/{project_name}", status_code=303)


@router.post("/{schedule_id}/edit")
def edit_schedule(
    request: Request,
    schedule_id: int,
    cron_expression: str = Form(...),
    timeout_seconds: int = Form(3600),
    entrypoint: str = Form(""),
    db: Session = Depends(get_db),
):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404)

    schedule.cron_expression = cron_expression
    schedule.timeout_seconds = timeout_seconds
    schedule.entrypoint = entrypoint.strip() or None
    db.commit()

    scheduler = _scheduler(request)
    if scheduler and schedule.enabled:
        scheduler.add_schedule(schedule_id, schedule.project_id, cron_expression, timeout_seconds)

    project_name = schedule.project.name if schedule.project else "unknown"
    return RedirectResponse(url=f"/projects/{project_name}", status_code=303)
