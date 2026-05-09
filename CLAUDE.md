# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Run dashboard locally (development)
uv run uvicorn dashboard.main:app --reload --port 8420

# Run as installed console script
uv run pyrunner

# Install/update dependencies
uv sync

# On Ubuntu target (production)
systemctl --user start pyrunner
systemctl --user restart pyrunner
systemctl --user status pyrunner
```

No test suite exists yet. Validate changes by running the server and exercising the dashboard at `http://localhost:8420`.

## Architecture Overview

PyRunner is a self-hosted task orchestrator for a single Ubuntu Desktop user. The FastAPI app (`dashboard/main.py`) is the entry point — it runs a lifespan context manager that starts two background services alongside the web server:

- **GitPoller** (`git_poller.py`) — polls registered Git repos every N minutes, detects new commits via `git fetch`, and runs a full deploy pipeline (`git pull` → `uv sync` → regenerate Supervisor config → restart process). This is the largest and most complex module.
- **SchedulerService** (`scheduler_service.py`) — wraps APScheduler; loads cron schedules from SQLite on startup and executes them by spawning `uv run <entrypoint>` subprocesses. Captures stdout/stderr to timestamped log files.

Process supervision is handled by **Supervisord** via XML-RPC. `supervisor_client.py` is a thin wrapper over the `http://localhost:9001/RPC2` endpoint — it auto-generates `.conf` files and calls reread/update/restart.

All state lives in SQLite (`~/pyrunner/data/pyrunner.db`) with SQLAlchemy models defined in `database.py`: `Project`, `Deploy`, `Schedule`, `Execution`, `ActivityEvent`.

## Request Flow

Web UI uses HTMX — most responses are HTML fragments returned by Jinja2 templates, not JSON. Route handlers live in `dashboard/routers/`. The HTMX pattern means partial page updates: look for `HX-Request` header checks or `hx-` attributes in templates to understand what each endpoint returns.

## Per-Project Configuration

Managed repos declare their own config in `[tool.pyrunner]` inside their `pyproject.toml`:

```toml
[tool.pyrunner]
entrypoint = "main.py"
type = "worker"           # worker | web | scheduled
auto_start = true
schedule = "0 9 * * *"   # cron (for scheduled type)
timeout = 600
env_file = ".env"
```

PyRunner reads this during clone/deploy to configure Supervisor and the scheduler.

## Runtime Layout (Ubuntu target)

```
~/pyrunner/
├── config.toml          # Global config (git poll interval, supervisor URL, timezone, etc.)
├── data/pyrunner.db     # SQLite database
├── projects/            # Cloned repos (one dir per project)
├── supervisor/conf.d/   # Auto-generated Supervisor .conf files
└── logs/                # Per-project stdout/stderr logs
```

## Key Design Decisions

- **No Docker, Redis, or Celery** — everything runs as native processes managed by Supervisord. Supervisor configs are generated programmatically by `git_poller.py`.
- **uv** is the only supported package manager. All subprocess calls use `uv run` or `uv sync`.
- **SSH key auth** for private repos is configured globally in `config.toml` (`[git].ssh_key_path`).
- `git_poller.py` runs deploy steps in a `ThreadPoolExecutor` — avoid blocking the main thread or introducing shared mutable state in that module.
- The XML-RPC client (`supervisor_client.py`) must handle fault codes gracefully — `ALREADY_STARTED` and `NOT_RUNNING` are not errors.
