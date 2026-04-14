PRODUCT REQUIREMENTS DOCUMENT

**PyRunner**

Local Python Task Orchestrator

with Git Sync, Scheduling & Dashboard

Version 1.0 • April 2026

Target Platform: Ubuntu Desktop 24.04+

1\. Overview

PyRunner is a lightweight, self-hosted task orchestration platform designed to run on a local Ubuntu Desktop machine. It provides a centralized dashboard for managing, scheduling, and monitoring small Python applications that perform a variety of tasks including browser automation, data collection, content processing, and background services.

The system uses uv as its package manager, Supervisord for process management, APScheduler for cron-like scheduling, and a Git polling mechanism for continuous deployment from GitHub repositories.

1.1 Problem Statement

Running multiple small Python scripts on a local machine currently requires manually managing virtual environments, writing systemd units or cron entries, checking logs via terminal, and pulling Git changes by hand. There is no unified view of what is running, what failed, or what needs updating. This friction slows down iteration and makes it easy to lose track of scripts.

1.2 Goals

-   Provide a single web dashboard to manage all Python projects on the machine

-   Automate deployment: push to GitHub, PyRunner detects changes and updates automatically

-   Enable cron-based scheduling with full UI control (create, edit, delete, pause)

-   Support both headless scripts and scripts that require browser/display access

-   Keep the system lightweight with minimal dependencies (no Docker, no Redis, no Celery)

-   Use uv for fast, reproducible dependency management per project

1.3 Non-Goals

-   Multi-machine / distributed orchestration (this is single-host only)

-   Container-based isolation (projects run in uv-managed venvs, not containers)

-   User authentication / multi-tenancy (single-user local tool)

-   Replacing Coolify or Railway for web-facing services

1.4 Target User

A technically proficient developer who runs multiple small Python automations on an Ubuntu Desktop machine and wants a clean, unified interface to manage them without the overhead of container orchestration or cloud platforms.

2\. Architecture

2.1 System Components

  -----------------------------------------------------------------------------------------------------------------
  **Component**     **Technology**               **Role**
  ----------------- ---------------------------- ------------------------------------------------------------------
  Dashboard         FastAPI + Jinja2 + HTMX      Web UI for project management, logs, scheduling

  Process Manager   Supervisord                  Start/stop/restart processes, auto-restart on crash, log capture

  Scheduler         APScheduler (SQLite store)   Persistent cron job management with UI control

  Package Manager   uv                           Per-project virtual environment and dependency resolution

  Deploy Engine     Git Poller (custom)          Periodic git fetch/pull with auto-redeploy on changes

  Database          SQLite                       Projects metadata, schedule history, deploy logs, config

  Reverse Proxy     Caddy (optional)             Route project UIs to subpaths if needed
  -----------------------------------------------------------------------------------------------------------------

2.2 High-Level Architecture

The system follows a layered architecture where the Dashboard acts as the control plane, communicating with Supervisord via its XML-RPC interface and managing the Scheduler and Deploy Engine as internal services.

  ------------------------------------------------------------------------------------------------
  **Layer**          **Description**
  ------------------ -----------------------------------------------------------------------------
  Presentation       FastAPI + Jinja2 + HTMX web interface served on localhost:8420

  Control Plane      REST API endpoints for project CRUD, schedule management, deploy triggers

  Process Layer      Supervisord managing individual project processes via generated .conf files

  Scheduling Layer   APScheduler running as a background thread in the dashboard process

  Deploy Layer       Git poller running as a background thread, checking repos every N minutes

  Storage Layer      SQLite database + filesystem (project dirs, logs, Supervisor configs)
  ------------------------------------------------------------------------------------------------

2.3 Directory Structure

All PyRunner data lives under a single root directory, defaulting to \~/pyrunner:

  ---------------------------------------------------------------------------------------------
  **Path**                         **Purpose**
  -------------------------------- ------------------------------------------------------------
  \~/pyrunner/dashboard/           Dashboard application source code

  \~/pyrunner/projects/            Cloned project repositories (one subdirectory per project)

  \~/pyrunner/supervisor/conf.d/   Auto-generated Supervisor config files

  \~/pyrunner/logs/                Per-project log files (stdout + stderr)

  \~/pyrunner/data/pyrunner.db     SQLite database

  \~/pyrunner/config.toml          Global PyRunner configuration
  ---------------------------------------------------------------------------------------------

3\. Core Features

3.1 Project Management

3.1.1 Project Registration

Users register a project by providing a GitHub repository URL (HTTPS or SSH) and an optional branch name (defaults to main). PyRunner then clones the repository into \~/pyrunner/projects/\<project-name\>/, detects the project configuration, and creates the necessary Supervisor config.

**Project Detection:** PyRunner looks for a pyproject.toml at the repository root. If present, it reads the \[project\] table for metadata (name, description, version). If a \[tool.pyrunner\] table exists, it reads PyRunner-specific configuration.

3.1.2 pyproject.toml Integration

Projects can optionally include a \[tool.pyrunner\] section in their pyproject.toml to declare runtime configuration:

  -----------------------------------------------------------------------------------------------
  **Key**            **Type**    **Default**   **Description**
  ------------------ ----------- ------------- --------------------------------------------------
  entrypoint         string      main.py       Script to execute as the main process

  type               string      worker        One of: worker, web, scheduled

  port               integer     null          Port number if the project exposes a web UI

  env_file           string      .env          Path to environment variables file

  auto_start         boolean     true          Start automatically when PyRunner boots

  requires_display   boolean     false         Whether the process needs DISPLAY access

  schedule           string      null          Default cron expression (e.g. \*/5 \* \* \* \*)

  timeout            integer     3600          Max execution time in seconds for scheduled runs
  -----------------------------------------------------------------------------------------------

3.1.3 Project Lifecycle

Each project moves through defined states managed by the dashboard:

  --------------------------------------------------------------------------
  **State**          **Description**
  ------------------ -------------------------------------------------------
  Registered         Repository URL added, not yet cloned

  Cloning            Git clone in progress

  Installing         Running uv sync to set up dependencies

  Ready              Installed and ready to run, not currently active

  Running            Active process managed by Supervisord

  Stopped            Manually stopped by user

  Error              Last run or deploy failed (see logs for details)

  Updating           Git pull + uv sync in progress after detected changes
  --------------------------------------------------------------------------

3.2 Git Polling & Auto-Deploy

3.2.1 Polling Mechanism

The Git Poller runs as a background thread inside the dashboard process. At a configurable interval (default: 5 minutes), it iterates over all registered projects and checks for upstream changes.

**Polling Flow:**

1.  For each project, run git fetch origin \<branch\> in the project directory

2.  Compare local HEAD with origin/\<branch\> using git rev-parse

3.  If commits differ, trigger the deploy pipeline for that project

4.  Record the check result (no change / updated / error) in the database

5.  On error (network failure, auth issue), log warning and retry next cycle

3.2.2 Deploy Pipeline

When changes are detected, the following steps execute sequentially:

1.  Set project state to Updating

2.  If project is running, send Supervisor stop command and wait for graceful shutdown (30s timeout)

3.  Run git pull origin \<branch\> \--ff-only (fast-forward only to avoid merge conflicts)

4.  Run uv sync to update dependencies based on the new pyproject.toml / uv.lock

5.  Regenerate Supervisor config if \[tool.pyrunner\] changed

6.  Run supervisorctl reread && supervisorctl update

7.  If the project was previously running (or auto_start is true), restart it

8.  Record deploy result (success/failure, commit hash, duration) in database

3.2.3 Git Configuration

  ----------------------------------------------------------------------------------------------------------------
  **Setting**             **Default**          **Description**
  ----------------------- -------------------- -------------------------------------------------------------------
  poll_interval_minutes   5                    How often to check all repos for changes

  deploy_strategy         ff-only              Git pull strategy (ff-only prevents merge conflicts)

  max_retries             3                    Max consecutive failures before disabling auto-poll for a project

  ssh_key_path            \~/.ssh/id_ed25519   SSH key for private repository access

  parallel_checks         4                    Max concurrent git fetch operations
  ----------------------------------------------------------------------------------------------------------------

3.3 Process Management (Supervisord)

3.3.1 Supervisor Integration

PyRunner communicates with Supervisord through its XML-RPC interface (default: http://localhost:9001/RPC2). The dashboard generates and manages Supervisor program configuration files automatically.

3.3.2 Generated Config Template

For each project, PyRunner generates a Supervisor .conf file in \~/pyrunner/supervisor/conf.d/ with the following structure:

  ----------------------------------------------------------------------------------------------------------------
  **Directive**      **Value**                              **Notes**
  ------------------ -------------------------------------- ------------------------------------------------------
  command            uv run \<entrypoint\>                  Runs the script within the project's uv-managed venv

  directory          \~/pyrunner/projects/\<name\>/         Working directory set to project root

  autostart          true/false                             Based on project auto_start setting

  autorestart        unexpected                             Restart only on unexpected exit (exit code != 0)

  startsecs          5                                      Process must run 5s to be considered started

  stopwaitsecs       30                                     Graceful shutdown timeout before SIGKILL

  stdout_logfile     \~/pyrunner/logs/\<name\>.stdout.log   Captured stdout

  stderr_logfile     \~/pyrunner/logs/\<name\>.stderr.log   Captured stderr

  environment        DISPLAY, ENV vars                      Includes DISPLAY=:0 if requires_display is true
  ----------------------------------------------------------------------------------------------------------------

3.3.3 Dashboard ↔ Supervisor Operations

  -----------------------------------------------------------------------------------------------
  **Dashboard Action**   **Supervisor API Call**      **Notes**
  ---------------------- ---------------------------- -------------------------------------------
  Start Project          startProcess(name)           Starts the configured program

  Stop Project           stopProcess(name)            Sends SIGTERM, then SIGKILL after timeout

  Restart Project        stopProcess + startProcess   Sequential stop then start

  View Status            getProcessInfo(name)         Returns state, PID, uptime, exit code

  View All               getAllProcessInfo()          Bulk status for dashboard overview

  Tail Logs              tailProcessStdoutLog(name)   Real-time log streaming
  -----------------------------------------------------------------------------------------------

3.4 Scheduling (APScheduler)

3.4.1 Scheduler Architecture

APScheduler runs as a background component within the dashboard's FastAPI process. It uses a SQLite job store for persistence, ensuring scheduled jobs survive dashboard restarts. When a scheduled job triggers, it spawns the project's script as a subprocess (not through Supervisor) to allow independent lifecycle tracking.

3.4.2 Schedule Management

The dashboard provides full CRUD for schedules through the web UI:

-   Create: Select a project, define a cron expression (or use presets), set optional timeout

-   Edit: Modify cron expression, timeout, or enabled/disabled state

-   Delete: Remove the schedule (does not affect the project itself)

-   Pause / Resume: Toggle a schedule without deleting it

-   Run Now: Trigger an immediate execution outside the schedule

3.4.3 Execution Model for Scheduled Runs

1.  APScheduler triggers at the scheduled time

2.  Dashboard spawns a subprocess: uv run \<entrypoint\> in the project directory

3.  Subprocess stdout/stderr are captured to a per-execution log file

4.  A timeout watchdog kills the process if it exceeds the configured timeout

5.  Execution result (exit code, duration, log path) is recorded in the database

6.  If the project has a webhook URL configured, send a completion notification

3.4.4 Schedule History

Every scheduled execution is logged in the database with the following fields: execution ID, project name, trigger time, start time, end time, duration, exit code, log file path, and status (success/failed/timeout/cancelled). The dashboard shows the last N executions per project with color-coded status indicators.

3.5 Browser Automation Support

3.5.1 Display Access

Since PyRunner runs on Ubuntu Desktop, projects that need browser access (Playwright, Selenium) can access the actual desktop display. When a project has requires_display: true in its \[tool.pyrunner\] config, the generated Supervisor config includes DISPLAY=:0 in its environment, granting access to the active X11/Wayland session.

3.5.2 Headless Fallback

For scheduled runs that may execute while no user session is active, PyRunner starts an Xvfb virtual framebuffer as a fallback. The startup script checks if DISPLAY=:0 is available; if not, it launches Xvfb on :99 and sets DISPLAY=:99 for the subprocess. This ensures browser automation works regardless of whether someone is logged into the desktop.

3.6 Web UI Projects

3.6.1 Routing

Projects of type web that expose a UI on a specific port are accessible through a reverse proxy. PyRunner generates a Caddy configuration block (or uses its own built-in proxy) to route /app/\<project-name\>/\* to localhost:\<project-port\>. This allows all project UIs to be accessed through the main PyRunner dashboard URL.

3.6.2 Dashboard Integration

Web-type projects show additional controls in the dashboard: an \"Open\" button that links directly to the project's UI, the project's port number and URL path, and health check status (HTTP GET to the project's port).

4\. Dashboard UI

4.1 Technology Stack

The dashboard uses FastAPI as the backend framework, Jinja2 for server-side HTML templating, HTMX for dynamic updates without a full SPA framework, and Tailwind CSS (via CDN) for styling. This combination provides a responsive, modern UI with minimal JavaScript complexity.

4.2 Pages & Views

4.2.1 Home / Overview

-   Summary cards: Total projects, Running, Stopped, Errored, Scheduled

-   Recent activity feed: Last 20 events (deploys, schedule executions, errors)

-   System health: Supervisor status, disk usage, Git poller status

4.2.2 Projects List

-   Table of all registered projects with columns: Name, Type, Status, Last Deploy, Git Branch, Actions

-   Quick actions: Start, Stop, Restart, View Logs, Open UI (for web type)

-   Filters: By status (running/stopped/error), by type (worker/web/scheduled)

-   Add Project button: Opens a modal to register a new GitHub repository

4.2.3 Project Detail

-   Project metadata: Name, description, repository URL, branch, entrypoint, type

-   Current status with uptime, PID, memory usage

-   Live log viewer with auto-scroll and search (HTMX polling or SSE)

-   Deploy history: Table of recent deploys with commit hash, timestamp, status

-   Schedule management: Create/edit/delete cron schedules for this project

-   Execution history: Table of recent scheduled runs with status and duration

-   Configuration editor: View/edit \[tool.pyrunner\] settings via the UI

-   Manual actions: Force Deploy, Run Now, Clear Logs

4.2.4 Schedules Overview

-   Global view of all schedules across all projects

-   Calendar/timeline view showing upcoming executions

-   Ability to create new schedules from this page

-   Execution history with filtering by project, status, date range

4.2.5 Deploy Log

-   Global view of all Git poll results and deploy events

-   Columns: Project, Timestamp, Old Commit, New Commit, Status, Duration

-   Filter by project, status, date range

4.2.6 Settings

-   Git Poller: poll interval, SSH key path, parallel check count

-   Supervisor: connection URL, log retention policy

-   Notifications: webhook URLs for deploy/schedule events

-   Appearance: theme (light/dark), timezone

5\. Data Model

5.1 SQLite Schema

5.1.1 projects

  ---------------------------------------------------------------------------
  **Column**         **Type**         **Description**
  ------------------ ---------------- ---------------------------------------
  id                 INTEGER PK       Auto-increment primary key

  name               TEXT UNIQUE      Project name (derived from repo name)

  repo_url           TEXT             GitHub repository URL

  branch             TEXT             Git branch to track (default: main)

  entrypoint         TEXT             Script to run (default: main.py)

  type               TEXT             worker \| web \| scheduled

  port               INTEGER NULL     Port for web-type projects

  auto_start         BOOLEAN          Start on PyRunner boot

  requires_display   BOOLEAN          Needs DISPLAY environment variable

  status             TEXT             Current lifecycle state

  last_commit        TEXT             SHA of last deployed commit

  created_at         DATETIME         Registration timestamp

  updated_at         DATETIME         Last modification timestamp
  ---------------------------------------------------------------------------

5.1.2 deploys

  -------------------------------------------------------------------------
  **Column**         **Type**         **Description**
  ------------------ ---------------- -------------------------------------
  id                 INTEGER PK       Auto-increment primary key

  project_id         INTEGER FK       Reference to projects table

  old_commit         TEXT             Previous commit SHA

  new_commit         TEXT             New commit SHA after pull

  status             TEXT             success \| failed \| skipped

  duration_seconds   REAL             Time taken for deploy pipeline

  error_message      TEXT NULL        Error details if failed

  triggered_by       TEXT             poll \| manual \| webhook

  created_at         DATETIME         Deploy timestamp
  -------------------------------------------------------------------------

5.1.3 schedules

  ------------------------------------------------------------------------
  **Column**        **Type**         **Description**
  ----------------- ---------------- -------------------------------------
  id                INTEGER PK       Auto-increment primary key

  project_id        INTEGER FK       Reference to projects table

  cron_expression   TEXT             Cron expression (5-field)

  timeout_seconds   INTEGER          Max execution time

  enabled           BOOLEAN          Whether schedule is active

  created_at        DATETIME         Creation timestamp

  updated_at        DATETIME         Last modification timestamp
  ------------------------------------------------------------------------

5.1.4 executions

  ------------------------------------------------------------------------------------------
  **Column**         **Type**         **Description**
  ------------------ ---------------- ------------------------------------------------------
  id                 INTEGER PK       Auto-increment primary key

  schedule_id        INTEGER FK       Reference to schedules table

  project_id         INTEGER FK       Reference to projects table

  trigger_time       DATETIME         When APScheduler fired

  start_time         DATETIME         When subprocess started

  end_time           DATETIME NULL    When subprocess finished

  duration_seconds   REAL NULL        Execution duration

  exit_code          INTEGER NULL     Process exit code

  status             TEXT             running \| success \| failed \| timeout \| cancelled

  log_path           TEXT             Path to execution log file

  created_at         DATETIME         Record creation timestamp
  ------------------------------------------------------------------------------------------

6\. Configuration

6.1 Global Config (config.toml)

PyRunner's global configuration lives in \~/pyrunner/config.toml. The dashboard reads this file on startup and provides a settings page to modify it.

  -------------------------------------------------------------------------------------------------------------
  **Section**      **Key**                 **Default**                     **Description**
  ---------------- ----------------------- ------------------------------- ------------------------------------
  \[server\]       host                    0.0.0.0                         Dashboard bind address

  \[server\]       port                    8420                            Dashboard port

  \[git\]          poll_interval_minutes   5                               Polling frequency

  \[git\]          ssh_key_path            \~/.ssh/id_ed25519              SSH key for private repos

  \[git\]          parallel_checks         4                               Concurrent git fetch ops

  \[git\]          max_retries             3                               Failures before disabling poll

  \[supervisor\]   config_dir              \~/pyrunner/supervisor/conf.d   Supervisor config location

  \[supervisor\]   socket                  unix:///tmp/supervisor.sock     Supervisor socket path

  \[scheduler\]    timezone                America/Sao_Paulo               Default timezone for schedules

  \[logs\]         retention_days          30                              Auto-delete logs older than N days

  \[logs\]         max_size_mb             50                              Max log file size before rotation
  -------------------------------------------------------------------------------------------------------------

7\. Installation & Setup

7.1 Prerequisites

-   Ubuntu Desktop 24.04 or later

-   Python 3.11+ (system or pyenv-managed)

-   uv installed globally (curl -LsSf https://astral.sh/uv/install.sh \| sh)

-   Git 2.x+ with SSH key configured for GitHub

-   Supervisord (apt install supervisor)

-   Xvfb (apt install xvfb) for headless browser automation fallback

7.2 Installation Steps

1.  Clone the PyRunner repository to \~/pyrunner/dashboard/

2.  Run the setup script: ./install.sh which installs system dependencies, creates directory structure, configures Supervisord to include \~/pyrunner/supervisor/conf.d/\*.conf, initializes the SQLite database, and creates a systemd user service for the dashboard itself

3.  Start PyRunner: systemctl \--user start pyrunner

4.  Access dashboard at http://localhost:8420

7.3 PyRunner as a Service

PyRunner's dashboard itself runs as a systemd user service, ensuring it starts on login and restarts on crash. The install script creates \~/.config/systemd/user/pyrunner.service which runs the FastAPI app via uvicorn with the working directory set to \~/pyrunner/dashboard/.

8\. Security Considerations

-   Network Binding: Dashboard binds to localhost by default. If exposed on the network, use Tailscale or a VPN. No built-in authentication since this is a single-user local tool.

-   SSH Keys: Private keys for Git access are never stored in the database. The config only references the filesystem path.

-   Environment Variables: .env files per project are read by Supervisord and never exposed through the dashboard API.

-   Process Isolation: Each project runs in its own uv-managed venv with its own user-space dependencies. No shared global packages.

-   Log Sanitization: The log viewer strips ANSI escape codes for display but preserves raw logs on disk.

9\. Future Considerations

The following features are explicitly out of scope for v1.0 but are tracked for future versions:

-   Webhook-based deploy (Option A): Accept GitHub webhooks for instant deploy on push, requiring either Tailscale Funnel or a Cloudflare Tunnel to expose the endpoint

-   Project templates: Scaffold new projects from templates (e.g., browser-automation-template, api-scraper-template) with pre-configured pyproject.toml

-   Resource monitoring: Per-project CPU, memory, and network usage tracking with alerts

-   Notifications: Slack, Telegram, or email notifications for deploy failures and schedule errors

-   Multi-branch support: Track and deploy from multiple branches with environment-based routing

-   Plugin system: Allow projects to register dashboard widgets or custom API endpoints

10\. Success Metrics

  --------------------------------------------------------------------------------------------------------
  **Metric**                **Target**          **Measurement**
  ------------------------- ------------------- ----------------------------------------------------------
  Deploy latency            \< 60 seconds       Time from git change detection to process restart

  Dashboard response time   \< 200ms            P95 page load time for all dashboard pages

  Schedule accuracy         \< 5 second drift   Difference between scheduled time and actual trigger

  Git poll overhead         \< 1% CPU           Average CPU usage of the polling background thread

  System uptime             99.9%               Dashboard availability measured over 30-day windows

  Project capacity          50+ projects        Dashboard remains responsive with 50 registered projects
  --------------------------------------------------------------------------------------------------------

11\. Appendix

11.1 Example pyproject.toml

Below is an example pyproject.toml for a browser automation project that posts content to TikTok on a schedule:

  ----------------------------------------------------------------------------------------------------
  **Section**                **Content**
  -------------------------- -------------------------------------------------------------------------
  \[project\]                name = \"tiktok-poster\", version = \"0.1.0\", requires-python \>= 3.12

  \[project\] dependencies   playwright \>= 1.40, httpx \>= 0.27

  \[tool.pyrunner\]          entrypoint = \"main.py\"

  \[tool.pyrunner\]          type = \"scheduled\"

  \[tool.pyrunner\]          requires_display = true

  \[tool.pyrunner\]          schedule = \"0 9,18 \* \* \*\" (9AM and 6PM daily)

  \[tool.pyrunner\]          timeout = 600 (10 minutes)
  ----------------------------------------------------------------------------------------------------

11.2 Technology Decisions

  ---------------------------------------------------------------------------------------------------------------------------------------------------
  **Decision**          **Chosen**      **Rationale**
  --------------------- --------------- -------------------------------------------------------------------------------------------------------------
  Process manager       Supervisord     Mature, XML-RPC API, auto-restart, log capture. Simpler than systemd units for managing many small scripts.

  Package manager       uv              10-100x faster than pip, built-in venv management, lockfile support, single binary.

  Scheduler             APScheduler     Embeddable, SQLite job store, cron triggers, no external broker needed. Lighter than Celery.

  Dashboard framework   FastAPI         Async support, auto-generated API docs, easy to embed background tasks.

  Frontend approach     Jinja2 + HTMX   Server-rendered with dynamic updates. No build step, no Node.js dependency in production.

  Database              SQLite          Zero-config, single file, more than sufficient for single-user local tool.

  Deploy mechanism      Git polling     Works offline, no inbound network required, simple to implement and debug.
  ---------------------------------------------------------------------------------------------------------------------------------------------------

*End of Document*