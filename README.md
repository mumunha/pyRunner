# PyRunner

Local Python Task Orchestrator with Git Sync, Scheduling & Dashboard

A self-hosted platform for managing, scheduling, and monitoring Python automations on an Ubuntu Desktop machine. Register a GitHub repository, and PyRunner handles cloning, dependency installation, process management, cron scheduling, and automatic redeployment when you push changes.

---

## Features

- **Web dashboard** — manage all projects from a single UI at `http://localhost:8420`
- **Git auto-deploy** — polls GitHub every N minutes; detects changes and redeploys automatically
- **Cron scheduling** — create, pause, and run schedules from the UI using APScheduler
- **Process management** — start/stop/restart via Supervisord; auto-restarts on crash
- **Live log viewer** — tail stdout/stderr directly in the dashboard
- **Browser automation support** — passes `DISPLAY=:0` to projects that need it; Xvfb fallback for headless runs
- **Per-project venvs** — uses `uv` for fast, reproducible dependency management
- **No Docker, no Redis, no Celery** — runs entirely on SQLite and system processes

---

## Requirements

| Requirement | Install |
|---|---|
| Ubuntu Desktop 24.04+ | — |
| Python 3.11+ | `sudo apt install python3` |
| uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Git 2.x+ | `sudo apt install git` |
| Supervisord | `sudo apt install supervisor` |
| Xvfb *(optional, browser automation)* | `sudo apt install xvfb` |

---

## Installation

### 1. Clone PyRunner

**Public repo:**

```bash
git clone https://github.com/your-username/pyrunner.git ~/pyrunner/dashboard
cd ~/pyrunner/dashboard
```

**Private repo — SSH (recommended):**

First, make sure you have an SSH key and it is added to your GitHub account:

```bash
# Generate a key if you don't have one
ssh-keygen -t ed25519 -C "your@email.com"

# Print the public key — copy this and add it to GitHub → Settings → SSH Keys
cat ~/.ssh/id_ed25519.pub

# Test the connection
ssh -T git@github.com
# Expected: "Hi username! You've successfully authenticated..."
```

Then clone using the SSH URL:

```bash
git clone git@github.com:your-username/pyrunner.git ~/pyrunner/dashboard
cd ~/pyrunner/dashboard
```

**Private repo — HTTPS with a Personal Access Token:**

Generate a token at GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic). Grant `repo` scope.

```bash
# Option A: embed token in the URL (not stored on disk after clone)
git clone https://YOUR_TOKEN@github.com/your-username/pyrunner.git ~/pyrunner/dashboard

# Option B: use Git credential helper so you're only prompted once
git config --global credential.helper store
git clone https://github.com/your-username/pyrunner.git ~/pyrunner/dashboard
# Enter your GitHub username and the token as the password when prompted
```

### 2. Run the installer

```bash
chmod +x install.sh
./install.sh
```

The installer will:

- Create the directory structure under `~/pyrunner/`
- Install Python dependencies with `uv sync`
- Copy the default `config.toml`
- Add a `[include]` directive to `/etc/supervisor/supervisord.conf`
- Create and enable a systemd user service at `~/.config/systemd/user/pyrunner.service`

### 3. Start PyRunner

```bash
systemctl --user start pyrunner
```

### 4. Open the dashboard

```
http://localhost:8420
```

---

## Directory Structure

After installation, all runtime data lives under `~/pyrunner/`:

```
~/pyrunner/
├── dashboard/          ← PyRunner source (symlinked from your clone)
├── projects/           ← Cloned project repositories (one dir per project)
├── supervisor/
│   └── conf.d/         ← Auto-generated Supervisor .conf files
├── logs/               ← stdout/stderr logs per project
├── data/
│   └── pyrunner.db     ← SQLite database (projects, schedules, deploys)
└── config.toml         ← Global configuration
```

---

## Configuration

Edit `~/pyrunner/config.toml` directly, or use the **Settings** page in the dashboard.

```toml
[server]
host = "0.0.0.0"
port = 8420

[git]
poll_interval_minutes = 5       # How often to check all repos for changes
ssh_key_path = "~/.ssh/id_ed25519"
parallel_checks = 4
max_retries = 3                 # Failures before auto-poll is disabled for a project

[supervisor]
xmlrpc_url = "http://localhost:9001/RPC2"
username = ""
password = ""

[scheduler]
timezone = "America/Sao_Paulo"  # IANA timezone for cron expressions

[logs]
retention_days = 30
max_size_mb = 50

[notifications]
deploy_webhook = ""             # POST on deploy events (Slack/Discord compatible)
schedule_webhook = ""
```

---

## Adding a Project

### Via the dashboard

1. Open `http://localhost:8420/projects`
2. Click **Add Project**
3. Enter the GitHub repository URL and branch
4. PyRunner clones the repo, runs `uv sync`, and starts the process automatically

### Via pyproject.toml

Projects can declare PyRunner-specific configuration in their own `pyproject.toml`:

```toml
[tool.pyrunner]
entrypoint = "main.py"        # Script to execute (default: main.py)
type = "scheduled"            # worker | web | scheduled
port = 8080                   # Only for web type
auto_start = true
requires_display = false      # Set true for Playwright/Selenium
schedule = "0 9,18 * * *"    # Default cron (9 AM and 6 PM daily)
timeout = 600                 # Max execution time in seconds
env_file = ".env"
```

PyRunner reads this on clone and again on every deploy.

---

## Project Types

| Type | Description |
|---|---|
| `worker` | Long-running background process managed by Supervisord |
| `web` | Exposes a web UI on a port; accessible via the dashboard |
| `scheduled` | Runs on a cron schedule; not kept alive between runs |

---

## SSH Access for Private Repositories

Configure your SSH key in `config.toml`:

```toml
[git]
ssh_key_path = "~/.ssh/id_ed25519"
```

Ensure the key is added to your GitHub account and loaded in the agent:

```bash
ssh-add ~/.ssh/id_ed25519
ssh -T git@github.com   # Verify
```

Use SSH repository URLs when adding private projects:

```
git@github.com:your-username/your-repo.git
```

---

## Managing the Service

```bash
# Start
systemctl --user start pyrunner

# Stop
systemctl --user stop pyrunner

# Restart
systemctl --user restart pyrunner

# View live logs
journalctl --user -u pyrunner -f

# Enable on login
systemctl --user enable pyrunner
```

---

## Development / Manual Start

Run without systemd for development:

```bash
cd ~/pyrunner/dashboard
uv run uvicorn dashboard.main:app --reload --port 8420
```

Or using the project entry point:

```bash
uv run pyrunner
```

Set `PYRUNNER_ROOT` to override the default data directory:

```bash
PYRUNNER_ROOT=/tmp/pyrunner-dev uv run pyrunner
```

---

## Supervisord Setup (Manual)

If the installer could not modify `/etc/supervisor/supervisord.conf`, add the include manually:

```bash
sudo nano /etc/supervisor/supervisord.conf
```

Add at the end:

```ini
[include]
files = /home/YOUR_USER/pyrunner/supervisor/conf.d/*.conf
```

Then reload:

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo systemctl reload supervisor
```

---

## Browser Automation Projects

For projects using Playwright or Selenium:

1. Set `requires_display = true` in `[tool.pyrunner]`
2. PyRunner injects `DISPLAY=:0` into the generated Supervisor config
3. For scheduled (headless) runs, start Xvfb before the script:

```bash
sudo apt install xvfb
Xvfb :99 -screen 0 1280x720x24 &
export DISPLAY=:99
```

PyRunner's scheduled executor checks if `:0` is available and falls back to `:99` (Xvfb) automatically when no desktop session is active.

---

## Security Notes

- The dashboard binds to `0.0.0.0` by default. On a laptop or shared machine, restrict it to `127.0.0.1` in `config.toml`, or protect it with **Tailscale** if remote access is needed.
- There is no built-in authentication — this is a single-user local tool.
- `.env` files per project are read by Supervisord and are never exposed through the dashboard API.
- SSH private keys are referenced by path only and never stored in the database.

---

## Troubleshooting

**Dashboard won't start**
```bash
journalctl --user -u pyrunner -f
# Check that uv is in PATH for the systemd session:
echo $PATH
# If not, add to ~/.bashrc and re-login, or set ExecStart with full uv path in the service file
```

**Supervisord connection refused**
```bash
sudo systemctl status supervisor
sudo systemctl start supervisor
# Verify XML-RPC is enabled in /etc/supervisor/supervisord.conf:
# [inet_http_server]
# port = 127.0.0.1:9001
```

**Git clone fails (auth error)**
```bash
# Test SSH auth
ssh -T git@github.com
# Check key path in config.toml matches the key added to GitHub
```

**`uv` not found during deploy**
```bash
# uv must be on PATH for the user that runs PyRunner
which uv
# If missing, reinstall:
curl -LsSf https://astral.sh/uv/install.sh | sh
# Then add to ~/.bashrc:
export PATH="$HOME/.cargo/bin:$PATH"
```

**Project stuck in "cloning" or "installing"**

Check the activity feed on the dashboard home page or the Execution history on the project detail page for error messages.

---

## License

MIT
