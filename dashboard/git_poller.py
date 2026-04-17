"""Git polling background service for auto-deploy."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from dashboard.config import get_logs_dir, get_projects_dir, get_supervisor_conf_dir, load_config

logger = logging.getLogger(__name__)


def _run_git(cmd: list[str], cwd: Path, ssh_key: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    env = os.environ.copy()
    if ssh_key:
        env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key} -o StrictHostKeyChecking=no"
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Git command timed out"
    except Exception as e:
        return -1, "", str(e)


def _generate_supervisor_conf(project, cfg: dict) -> str:
    """Generate supervisor config for a project."""
    project_dir = get_projects_dir() / project.name
    log_dir = get_logs_dir()
    env_parts = []
    if project.requires_display:
        env_parts.append("DISPLAY=%(ENV_DISPLAY)s")
    env_str = ",".join(env_parts) if env_parts else ""
    env_line = f"environment={env_str}" if env_str else ""

    conf = f"""[program:{project.name}]
command=uv run {project.entrypoint}
directory={project_dir}
autostart={'true' if project.auto_start else 'false'}
autorestart=unexpected
startsecs=5
stopwaitsecs=30
stdout_logfile={log_dir}/{project.name}.stdout.log
stderr_logfile={log_dir}/{project.name}.stderr.log
stdout_logfile_maxbytes=50MB
stderr_logfile_maxbytes=50MB
stdout_logfile_backups=5
stderr_logfile_backups=5
"""
    if env_line:
        conf += env_line + "\n"
    return conf


def write_supervisor_conf(project, cfg: dict) -> Path:
    """Write supervisor config file for a project, return path."""
    conf_dir = get_supervisor_conf_dir()
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_path = conf_dir / f"{project.name}.conf"
    conf_path.write_text(_generate_supervisor_conf(project, cfg))
    return conf_path


def _supervisorctl(cfg: dict, *args) -> tuple[int, str]:
    """Execute a supervisord action via XML-RPC (avoids Unix socket permission issues)."""
    import xmlrpc.client
    from dashboard.supervisor_client import SupervisorClient
    action = args[0] if args else ""
    name = args[1] if len(args) > 1 else None
    try:
        client = SupervisorClient(cfg)
        srv = client._server.supervisor
        if action == "reread":
            srv.reloadConfig()
            return 0, "reread OK"
        elif action == "update":
            ok = client.update()
            return (0 if ok else 1), ("" if ok else "update failed — check supervisord logs")
        elif action in ("restart", "start", "stop"):
            if not name:
                return -1, f"supervisorctl {action} requires a process name"
            try:
                if action == "restart":
                    try:
                        srv.stopProcess(name, True)
                    except xmlrpc.client.Fault:
                        pass  # not running — that's fine
                    srv.startProcess(name, True)
                elif action == "start":
                    srv.startProcess(name, True)
                else:
                    srv.stopProcess(name, True)
                return 0, ""
            except xmlrpc.client.Fault as e:
                return 1, e.faultString
        else:
            return -1, f"Unknown supervisorctl action: {action}"
    except Exception as e:
        return -1, str(e)


class DeployPipeline:
    """Executes the full deploy pipeline for a single project."""

    def __init__(self, project, cfg: dict):
        self.project = project
        self.cfg = cfg
        self.project_dir = get_projects_dir() / project.name
        self.ssh_key = cfg["git"].get("ssh_key_path", "")

    def run(self) -> tuple[bool, str, str | None]:
        """Run deploy. Returns (success, new_commit_sha, error_message)."""
        from dashboard.database import Deploy, get_session_factory
        SessionLocal = get_session_factory()

        start_time = time.time()
        old_commit = self._get_local_head()

        db = SessionLocal()
        deploy = Deploy(
            project_id=self.project.id,
            old_commit=old_commit,
            status="pending",
            triggered_by="poll",
        )
        db.add(deploy)
        db.commit()
        db.refresh(deploy)
        deploy_id = deploy.id
        db.close()

        try:
            # Step 1: git pull
            rc, stdout, stderr = _run_git(
                ["git", "pull", "origin", self.project.branch, "--ff-only"],
                cwd=self.project_dir,
                ssh_key=self.ssh_key,
            )
            if rc != 0:
                raise RuntimeError(f"git pull failed: {stderr}")

            new_commit = self._get_local_head()

            # Step 2: uv sync
            result = subprocess.run(
                ["uv", "sync"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.warning("uv sync warning for %s: %s", self.project.name, result.stderr)

            # Step 3: Regenerate supervisor conf
            write_supervisor_conf(self.project, self.cfg)
            rc_reread, out_reread = _supervisorctl(self.cfg, "reread")
            rc_update, out_update = _supervisorctl(self.cfg, "update")
            logger.info("supervisorctl reread: rc=%s %s", rc_reread, out_reread)
            logger.info("supervisorctl update: rc=%s %s", rc_update, out_update)

            # Step 4: Restart if was running or auto_start
            if self.project.status in ("running", "updating") or self.project.auto_start:
                rc_restart, out_restart = _supervisorctl(self.cfg, "restart", self.project.name)
                logger.info("supervisorctl restart %s: rc=%s %s", self.project.name, rc_restart, out_restart)
                if rc_restart != 0:
                    # start may work even if restart fails (program not yet known)
                    rc_start, out_start = _supervisorctl(self.cfg, "start", self.project.name)
                    logger.info("supervisorctl start %s: rc=%s %s", self.project.name, rc_start, out_start)

            duration = time.time() - start_time

            # Verify the process is actually running via Supervisord XML-RPC
            actual_running = _verify_process_running(self.project.name, self.cfg)
            logger.info(
                "Process verification for %s: running=%s", self.project.name, actual_running
            )

            db = SessionLocal()
            deploy = db.query(Deploy).filter(Deploy.id == deploy_id).first()
            if deploy:
                deploy.new_commit = new_commit
                deploy.status = "success"
                deploy.duration_seconds = duration
                db.commit()
            db.close()

            return True, new_commit, None if actual_running else "Deploy succeeded but process did not start. Check supervisor logs."

        except Exception as e:
            duration = time.time() - start_time
            err = str(e)
            db = SessionLocal()
            deploy = db.query(Deploy).filter(Deploy.id == deploy_id).first()
            if deploy:
                deploy.status = "failed"
                deploy.error_message = err
                deploy.duration_seconds = duration
                db.commit()
            db.close()
            return False, old_commit or "", err

    def _get_local_head(self) -> str | None:
        rc, stdout, _ = _run_git(["git", "rev-parse", "HEAD"], cwd=self.project_dir)
        return stdout if rc == 0 else None


class GitPoller:
    """Background thread that polls git repos for changes."""

    def __init__(self, cfg: dict, scheduler_service=None):
        self.cfg = cfg
        self.scheduler_service = scheduler_service
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.running = False

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="git-poller")
        self._thread.start()
        self.running = True
        logger.info("Git poller started")

    def stop(self):
        self._stop_event.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Git poller stopped")

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._poll_all()
            except Exception as e:
                logger.error("Git poller error: %s", e)
            interval = self.cfg["git"]["poll_interval_minutes"] * 60
            self._stop_event.wait(timeout=interval)

    def _poll_all(self):
        from dashboard.database import Project, get_session_factory
        SessionLocal = get_session_factory()
        db = SessionLocal()
        try:
            projects = db.query(Project).filter(
                Project.status.notin_(["registered", "cloning", "installing"])
            ).all()
            project_data = [(p.id, p.name, p.branch, p.git_retries) for p in projects]
        finally:
            db.close()

        ssh_key = self.cfg["git"].get("ssh_key_path", "")
        max_retries = self.cfg["git"].get("max_retries", 3)

        for project_id, name, branch, retries in project_data:
            if retries >= max_retries:
                logger.warning("Skipping %s: too many consecutive git failures", name)
                continue
            try:
                self._check_project(project_id, name, branch, ssh_key)
            except Exception as e:
                logger.error("Error polling %s: %s", name, e)

    def _check_project(self, project_id: int, name: str, branch: str, ssh_key: str):
        from dashboard.database import ActivityEvent, Project, get_session_factory
        SessionLocal = get_session_factory()
        project_dir = get_projects_dir() / name

        if not project_dir.exists():
            return

        # Fetch
        rc, _, stderr = _run_git(
            ["git", "fetch", "origin", branch],
            cwd=project_dir,
            ssh_key=ssh_key,
        )
        if rc != 0:
            logger.warning("git fetch failed for %s: %s", name, stderr)
            db = SessionLocal()
            p = db.query(Project).filter(Project.id == project_id).first()
            if p:
                p.git_retries = (p.git_retries or 0) + 1
                db.commit()
            db.close()
            return

        # Compare
        rc_local, local_head, _ = _run_git(["git", "rev-parse", "HEAD"], cwd=project_dir)
        rc_remote, remote_head, _ = _run_git(
            ["git", "rev-parse", f"origin/{branch}"], cwd=project_dir
        )

        if rc_local != 0 or rc_remote != 0:
            return

        # Reset retry count on successful fetch
        db = SessionLocal()
        p = db.query(Project).filter(Project.id == project_id).first()
        if p:
            p.git_retries = 0
            db.commit()
        db.close()

        if local_head == remote_head:
            return  # No changes

        logger.info("Changes detected for %s: %s -> %s", name, local_head[:8], remote_head[:8])

        # Run deploy pipeline
        db = SessionLocal()
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            db.close()
            return

        prev_status = project.status
        project.status = "updating"
        db.commit()

        pipeline = DeployPipeline(project, self.cfg)
        db.close()

        success, new_commit, error = pipeline.run()

        db = SessionLocal()
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.last_commit = new_commit
            if success:
                project.status = "running" if prev_status == "running" or project.auto_start else "ready"
                project.error_message = None
                _log_activity(db, "deploy", name, f"Deployed {name} to {new_commit[:8] if new_commit else '?'}", "success")
            else:
                project.status = "error"
                project.error_message = error
                _log_activity(db, "deploy", name, f"Deploy failed for {name}: {error}", "error")
            project.updated_at = datetime.utcnow()
            db.commit()
        db.close()

    def trigger_deploy(self, project_id: int):
        """Manually trigger a deploy for a specific project."""
        from dashboard.database import ActivityEvent, Project, get_session_factory
        SessionLocal = get_session_factory()
        db = SessionLocal()
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            db.close()
            return

        prev_status = project.status
        project.status = "updating"
        db.commit()
        pipeline = DeployPipeline(project, self.cfg)
        db.close()

        def _run():
            success, new_commit, error = pipeline.run()
            db2 = SessionLocal()
            p = db2.query(Project).filter(Project.id == project_id).first()
            if p:
                p.last_commit = new_commit
                if not success:
                    p.status = "error"
                    p.error_message = error
                elif prev_status == "running" or p.auto_start:
                    # Verify the process actually started
                    time.sleep(2)
                    p.status = "running" if _verify_process_running(p.name, self.cfg) else "error"
                    if p.status == "error":
                        p.error_message = "Deploy succeeded but process did not start. Check supervisor logs."
                else:
                    p.status = "ready"
                    p.error_message = None
                p.updated_at = datetime.utcnow()
                db2.commit()
                _log_activity(
                    db2,
                    "deploy",
                    p.name,
                    f"Manual deploy {'succeeded' if success else 'failed'} for {p.name}",
                    "success" if success else "error",
                )
                db2.commit()
            db2.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def clone_project(self, project_id: int):
        """Clone a project repo and set up supervisor config."""
        from dashboard.database import ActivityEvent, Project, get_session_factory
        SessionLocal = get_session_factory()

        def _run():
            db = SessionLocal()
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                db.close()
                return

            project.status = "cloning"
            db.commit()
            name = project.name
            repo_url = project.repo_url
            branch = project.branch
            db.close()

            project_dir = get_projects_dir() / name
            ssh_key = self.cfg["git"].get("ssh_key_path", "")

            env = os.environ.copy()
            if ssh_key:
                env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key} -o StrictHostKeyChecking=no"

            try:
                result = subprocess.run(
                    ["git", "clone", "--branch", branch, "--single-branch", repo_url, str(project_dir)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"git clone failed: {result.stderr}")

                db = SessionLocal()
                project = db.query(Project).filter(Project.id == project_id).first()
                project.status = "installing"
                db.commit()
                db.close()

                # Read pyproject.toml if available
                _update_from_pyproject(project_id)

                # uv sync
                uv_result = subprocess.run(
                    ["uv", "sync"],
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                db = SessionLocal()
                project = db.query(Project).filter(Project.id == project_id).first()

                # Get commit hash
                rc, head, _ = _run_git(["git", "rev-parse", "HEAD"], cwd=project_dir)
                if rc == 0:
                    project.last_commit = head

                if uv_result.returncode != 0:
                    logger.warning("uv sync had issues for %s: %s", name, uv_result.stderr)

                # Write supervisor conf
                write_supervisor_conf(project, self.cfg)
                rc_rr, out_rr = _supervisorctl(self.cfg, "reread")
                rc_up, out_up = _supervisorctl(self.cfg, "update")
                logger.info("supervisorctl reread: rc=%s %s", rc_rr, out_rr)
                logger.info("supervisorctl update: rc=%s %s", rc_up, out_up)

                if project.auto_start:
                    rc_st, out_st = _supervisorctl(self.cfg, "start", name)
                    logger.info("supervisorctl start %s: rc=%s %s", name, rc_st, out_st)
                    # Small delay to let supervisord spin up the process
                    time.sleep(2)
                    if _verify_process_running(name, self.cfg):
                        project.status = "running"
                    else:
                        project.status = "error"
                        project.error_message = (
                            f"supervisorctl start returned rc={rc_st}: {out_st}. "
                            "Check that supervisord is running and its include path covers "
                            "~/pyrunner/supervisor/conf.d/"
                        )
                        logger.error("Process did not start for %s: %s", name, project.error_message)
                else:
                    project.status = "ready"

                db.commit()
                _log_activity(db, "deploy", name, f"Project {name} cloned and installed", "success")
                db.commit()
                db.close()

            except Exception as e:
                logger.error("Failed to clone %s: %s", name, e)
                db = SessionLocal()
                project = db.query(Project).filter(Project.id == project_id).first()
                if project:
                    project.status = "error"
                    project.error_message = str(e)
                    db.commit()
                    _log_activity(db, "deploy", name, f"Clone failed for {name}: {e}", "error")
                    db.commit()
                db.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()


def _verify_process_running(name: str, cfg: dict) -> bool:
    """Ask Supervisord via XML-RPC whether the process is actually running."""
    try:
        from dashboard.supervisor_client import SupervisorClient
        client = SupervisorClient(cfg)
        info = client.get_process_info(name)
        if info:
            return str(info.get("statename", "")).upper() == "RUNNING"
    except Exception as e:
        logger.warning("Could not verify process state for %s: %s", name, e)
    return False


def _update_from_pyproject(project_id: int):
    """Read [tool.pyrunner] from pyproject.toml and update project config."""
    from dashboard.database import Project, get_session_factory
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    SessionLocal = get_session_factory()
    db = SessionLocal()
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        db.close()
        return

    pyproject_path = get_projects_dir() / project.name / "pyproject.toml"
    db.close()

    if not pyproject_path.exists():
        return

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        tool_cfg = data.get("tool", {}).get("pyrunner", {})
        if not tool_cfg:
            return

        db = SessionLocal()
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            if "entrypoint" in tool_cfg:
                project.entrypoint = tool_cfg["entrypoint"]
            if "type" in tool_cfg:
                project.type = tool_cfg["type"]
            if "port" in tool_cfg:
                project.port = tool_cfg["port"]
            if "auto_start" in tool_cfg:
                project.auto_start = tool_cfg["auto_start"]
            if "requires_display" in tool_cfg:
                project.requires_display = tool_cfg["requires_display"]
            if "schedule" in tool_cfg:
                project.schedule = tool_cfg["schedule"]
            if "timeout" in tool_cfg:
                project.timeout_seconds = tool_cfg["timeout"]
            if "env_file" in tool_cfg:
                project.env_file = tool_cfg["env_file"]
            db.commit()
        db.close()
    except Exception as e:
        logger.warning("Failed to read pyproject.toml for project %s: %s", project_id, e)


def _log_activity(db, event_type: str, project_name: str, message: str, level: str = "info"):
    from dashboard.database import ActivityEvent
    db.add(ActivityEvent(event_type=event_type, project_name=project_name, message=message, level=level))
