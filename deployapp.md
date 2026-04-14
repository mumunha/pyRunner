# Deploying Your App to PyRunner

This guide is for developers who want their Python project managed by PyRunner — a local task orchestrator that handles cloning, dependency installation, process management, scheduling, and automatic redeployment.

---

## What PyRunner Does for Your App

Once registered, PyRunner will:

- Clone your repository and install dependencies automatically
- Start and keep your process alive (auto-restart on crash)
- Poll your GitHub repository every few minutes and redeploy on new commits
- Run your script on a cron schedule if configured
- Stream your logs to a web dashboard

---

## Requirements for Your App

Your app must be a **Python project hosted on GitHub** with a `pyproject.toml` at the root. That's the only hard requirement. Everything else is optional but recommended.

---

## Step 1 — Add a pyproject.toml

If your project doesn't have one yet, create it at the repository root:

```toml
[project]
name = "my-app"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    # add your dependencies here
]
```

PyRunner uses `uv sync` to install dependencies from this file. It also reads a `[tool.pyrunner]` section if present (see Step 2).

---

## Step 2 — Configure PyRunner (Optional but Recommended)

Add a `[tool.pyrunner]` section to your `pyproject.toml` to tell PyRunner how to run your app:

```toml
[tool.pyrunner]
entrypoint = "main.py"       # script to run (default: main.py)
type = "worker"              # worker | web | scheduled
auto_start = true            # start automatically when PyRunner boots
requires_display = false     # set true if you use Playwright or Selenium
```

If you skip this section, PyRunner uses sensible defaults (`entrypoint = main.py`, `type = worker`).

### Config reference

| Key | Type | Default | Description |
|---|---|---|---|
| `entrypoint` | string | `main.py` | Script PyRunner runs with `uv run <entrypoint>` |
| `type` | string | `worker` | `worker`, `web`, or `scheduled` |
| `port` | integer | — | Port your app listens on (web type only) |
| `auto_start` | boolean | `true` | Start automatically on PyRunner boot |
| `requires_display` | boolean | `false` | Inject `DISPLAY=:0` for browser automation |
| `schedule` | string | — | Default cron expression (e.g. `0 9 * * *`) |
| `timeout` | integer | `3600` | Max execution time in seconds for scheduled runs |
| `env_file` | string | `.env` | Path to environment variables file |

---

## App Types

### Worker — long-running background process

```toml
[tool.pyrunner]
type = "worker"
entrypoint = "main.py"
```

PyRunner starts your script and keeps it alive. If it crashes, it restarts automatically. Use this for scrapers, bots, listeners, or any service that runs continuously.

### Scheduled — runs on a cron schedule

```toml
[tool.pyrunner]
type = "scheduled"
entrypoint = "main.py"
schedule = "0 9,18 * * *"   # 9 AM and 6 PM every day
timeout = 600                # kill after 10 minutes
```

PyRunner starts your script at the scheduled time, waits for it to finish, and logs the result. The process is not kept alive between runs.

Common cron expressions:

| Expression | Meaning |
|---|---|
| `* * * * *` | Every minute |
| `*/15 * * * *` | Every 15 minutes |
| `0 * * * *` | Every hour |
| `0 9 * * *` | Every day at 9 AM |
| `0 9,18 * * *` | 9 AM and 6 PM daily |
| `0 0 * * 1` | Every Monday at midnight |

### Web — exposes a UI on a port

```toml
[tool.pyrunner]
type = "web"
entrypoint = "main.py"
port = 8080
```

PyRunner starts your app and shows an **Open** button in the dashboard linking to `http://localhost:8080`.

---

## Step 3 — Environment Variables

Create a `.env` file at the repository root with your secrets and config:

```
API_KEY=your-secret-key
DATABASE_URL=sqlite:///data.db
DEBUG=false
```

PyRunner passes these to your process via Supervisord. The `.env` file is **never exposed** through the dashboard.

Make sure `.env` is in your `.gitignore`:

```
.env
*.db
__pycache__/
.venv/
```

---

## Step 4 — Browser Automation (Playwright / Selenium)

If your app controls a browser, set `requires_display = true`:

```toml
[tool.pyrunner]
type = "scheduled"
entrypoint = "main.py"
requires_display = true
schedule = "0 10 * * *"
timeout = 300
```

PyRunner will inject `DISPLAY=:0` so your script can access the desktop display. For scheduled runs that run while no one is logged in, PyRunner falls back to Xvfb automatically.

Your script doesn't need any special code for this — just use Playwright or Selenium normally:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()  # headless=False works too with DISPLAY set
    page = browser.new_page()
    page.goto("https://example.com")
    ...
```

---

## Step 5 — Register in PyRunner

Once your repository is on GitHub:

1. Open the PyRunner dashboard at `http://localhost:8420`
2. Go to **Projects → Add Project**
3. Paste your repository URL (HTTPS or SSH)
4. Set the branch (default: `main`)
5. Click **Clone & Register**

PyRunner will clone the repo, run `uv sync`, read your `[tool.pyrunner]` config, and start the process if `auto_start = true`.

---

## Auto-Deploy on Push

PyRunner polls your repository every few minutes (default: 5). When it detects a new commit on your tracked branch, it:

1. Stops your process
2. Runs `git pull`
3. Runs `uv sync` to update dependencies
4. Restarts your process

You don't need to do anything — just push to GitHub and PyRunner handles the rest.

---

## Full Example

A browser automation project that posts to social media at 9 AM and 6 PM daily:

```toml
[project]
name = "social-poster"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "playwright>=1.40",
    "httpx>=0.27",
    "python-dotenv>=1.0",
]

[tool.pyrunner]
entrypoint = "main.py"
type = "scheduled"
requires_display = true
schedule = "0 9,18 * * *"
timeout = 600
env_file = ".env"
```

```
my-repo/
├── pyproject.toml
├── .env              ← secrets (gitignored)
├── .gitignore
└── main.py           ← your script
```

---

## Checklist Before Registering

- [ ] `pyproject.toml` exists at the repository root
- [ ] All dependencies are listed under `[project] dependencies`
- [ ] `[tool.pyrunner]` section added with `type`, `entrypoint`, and `schedule` if needed
- [ ] `.env` file created with secrets (and added to `.gitignore`)
- [ ] Repository is on GitHub and accessible from the Ubuntu machine (SSH key configured if private)
- [ ] Script exits with code `0` on success (important for scheduled type — non-zero is logged as failure)
