"""APScheduler-based scheduling service for PyRunner."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("APScheduler not installed. Scheduling disabled.")


class SchedulerService:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.timezone = cfg["scheduler"].get("timezone", "UTC")
        self._scheduler = None
        self._lock = threading.Lock()

    def start(self):
        if not HAS_APSCHEDULER:
            return

        from dashboard.config import get_db_path
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")
        }
        self._scheduler = BackgroundScheduler(jobstores=jobstores, timezone=self.timezone)
        self._scheduler.start()
        logger.info("Scheduler started (timezone: %s)", self.timezone)

        # Re-register all enabled schedules from DB
        self._reload_schedules()

    def stop(self):
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def _reload_schedules(self):
        """Load all enabled schedules from DB and register them."""
        from dashboard.database import Schedule, get_session_factory
        SessionLocal = get_session_factory()
        db = SessionLocal()
        try:
            schedules = db.query(Schedule).filter(Schedule.enabled == True).all()
            for s in schedules:
                self._add_job(s.id, s.project_id, s.cron_expression, s.timeout_seconds)
        finally:
            db.close()

    def add_schedule(self, schedule_id: int, project_id: int, cron_expr: str, timeout: int) -> bool:
        """Add a new schedule."""
        if not self._scheduler:
            return False
        try:
            self._add_job(schedule_id, project_id, cron_expr, timeout)
            return True
        except Exception as e:
            logger.error("Failed to add schedule %s: %s", schedule_id, e)
            return False

    def _add_job(self, schedule_id: int, project_id: int, cron_expr: str, timeout: int):
        if not self._scheduler:
            return
        job_id = f"schedule_{schedule_id}"
        # Remove existing if present
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {cron_expr}")
        minute, hour, day, month, day_of_week = parts
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=self.timezone,
        )
        self._scheduler.add_job(
            func=_execute_scheduled_job,
            trigger=trigger,
            args=[schedule_id, project_id, timeout],
            id=job_id,
            name=f"Project {project_id} schedule {schedule_id}",
            replace_existing=True,
            misfire_grace_time=300,
        )

    def remove_schedule(self, schedule_id: int):
        if not self._scheduler:
            return
        try:
            self._scheduler.remove_job(f"schedule_{schedule_id}")
        except Exception:
            pass

    def pause_schedule(self, schedule_id: int):
        if not self._scheduler:
            return
        try:
            self._scheduler.pause_job(f"schedule_{schedule_id}")
        except Exception as e:
            logger.warning("pause_schedule %s: %s", schedule_id, e)

    def resume_schedule(self, schedule_id: int):
        if not self._scheduler:
            return
        try:
            self._scheduler.resume_job(f"schedule_{schedule_id}")
        except Exception as e:
            logger.warning("resume_schedule %s: %s", schedule_id, e)

    def run_now(self, schedule_id: int, project_id: int, timeout: int):
        """Trigger immediate execution outside schedule."""
        t = threading.Thread(
            target=_execute_scheduled_job,
            args=[schedule_id, project_id, timeout],
            daemon=True,
        )
        t.start()

    def get_next_run(self, schedule_id: int) -> datetime | None:
        if not self._scheduler:
            return None
        try:
            job = self._scheduler.get_job(f"schedule_{schedule_id}")
            if job:
                return job.next_run_time
        except Exception:
            pass
        return None


def _execute_scheduled_job(schedule_id: int, project_id: int, timeout: int):
    """Execute a scheduled project run."""
    from dashboard.database import ActivityEvent, Execution, Project, get_session_factory
    from dashboard.config import get_logs_dir, get_projects_dir

    SessionLocal = get_session_factory()
    trigger_time = datetime.utcnow()

    from dashboard.database import Schedule

    db = SessionLocal()
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        db.close()
        return

    project_name = project.name
    schedule_obj = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    entrypoint = (schedule_obj.entrypoint if schedule_obj and schedule_obj.entrypoint else None) or project.entrypoint
    project_dir = get_projects_dir() / project_name
    db.close()

    logs_dir = get_logs_dir()
    log_file = logs_dir / f"{project_name}.exec.{int(time.time())}.log"

    execution = Execution(
        schedule_id=schedule_id,
        project_id=project_id,
        trigger_time=trigger_time,
        start_time=datetime.utcnow(),
        status="running",
        log_path=str(log_file),
    )
    db = SessionLocal()
    db.add(execution)
    db.commit()
    db.refresh(execution)
    exec_id = execution.id
    db.close()

    start_ts = time.time()
    proc = None
    exit_code = None
    status = "failed"

    try:
        with open(log_file, "w") as lf:
            proc = subprocess.Popen(
                ["uv", "run", entrypoint],
                cwd=project_dir,
                stdout=lf,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                exit_code = proc.wait(timeout=timeout)
                status = "success" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                exit_code = -1
                status = "timeout"

    except Exception as e:
        logger.error("Execution error for %s: %s", project_name, e)
        status = "failed"
        if log_file.exists():
            with open(log_file, "a") as lf:
                lf.write(f"\nError: {e}\n")

    duration = time.time() - start_ts

    db = SessionLocal()
    exc = db.query(Execution).filter(Execution.id == exec_id).first()
    if exc:
        exc.end_time = datetime.utcnow()
        exc.duration_seconds = duration
        exc.exit_code = exit_code
        exc.status = status
        db.commit()
    db.add(ActivityEvent(
        event_type="execution",
        project_name=project_name,
        message=f"Scheduled run {status}: {project_name} ({duration:.1f}s)",
        level="success" if status == "success" else "error",
    ))
    db.commit()
    db.close()

    logger.info("Execution %s for %s: %s in %.1fs", exec_id, project_name, status, duration)
