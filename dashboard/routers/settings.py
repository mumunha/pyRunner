"""Settings routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard.config import load_config, save_config

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def settings_page(request: Request):
    templates = request.app.state.templates
    cfg = load_config()
    return templates.TemplateResponse(request, "settings.html", {"cfg": cfg})


@router.post("/save")
def save_settings(
    request: Request,
    poll_interval_minutes: int = Form(5),
    ssh_key_path: str = Form("~/.ssh/id_ed25519"),
    parallel_checks: int = Form(4),
    max_retries: int = Form(3),
    xmlrpc_url: str = Form("http://localhost:9001/RPC2"),
    supervisor_username: str = Form(""),
    supervisor_password: str = Form(""),
    timezone: str = Form("America/Sao_Paulo"),
    retention_days: int = Form(30),
    max_size_mb: int = Form(50),
    deploy_webhook: str = Form(""),
    schedule_webhook: str = Form(""),
):
    cfg = load_config()
    cfg["git"]["poll_interval_minutes"] = poll_interval_minutes
    cfg["git"]["ssh_key_path"] = ssh_key_path
    cfg["git"]["parallel_checks"] = parallel_checks
    cfg["git"]["max_retries"] = max_retries
    cfg["supervisor"]["xmlrpc_url"] = xmlrpc_url
    cfg["supervisor"]["username"] = supervisor_username
    cfg["supervisor"]["password"] = supervisor_password
    cfg["scheduler"]["timezone"] = timezone
    cfg["logs"]["retention_days"] = retention_days
    cfg["logs"]["max_size_mb"] = max_size_mb
    cfg["notifications"]["deploy_webhook"] = deploy_webhook
    cfg["notifications"]["schedule_webhook"] = schedule_webhook
    save_config(cfg)

    # Update git poller interval
    try:
        git_poller = request.app.state.git_poller
        if git_poller:
            git_poller.cfg = cfg
    except Exception:
        pass

    return RedirectResponse(url="/settings", status_code=303)
