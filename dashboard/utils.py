"""Utility functions for PyRunner."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text for safe display."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """Run a subprocess command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)


def short_commit(sha: str | None) -> str:
    """Return shortened commit hash."""
    if not sha:
        return "—"
    return sha[:8]


def format_duration(seconds: float | None) -> str:
    """Format duration in human-readable form."""
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def status_color(status: str) -> str:
    """Return Tailwind color class for a status string."""
    return {
        "running": "text-green-400",
        "ready": "text-blue-400",
        "stopped": "text-gray-400",
        "error": "text-red-400",
        "updating": "text-yellow-400",
        "cloning": "text-yellow-400",
        "installing": "text-yellow-400",
        "registered": "text-gray-400",
        "success": "text-green-400",
        "failed": "text-red-400",
        "timeout": "text-orange-400",
        "cancelled": "text-gray-400",
        "pending": "text-yellow-400",
    }.get(status, "text-gray-400")


def status_bg(status: str) -> str:
    """Return Tailwind bg+text badge classes for a status string."""
    return {
        "running": "bg-green-900 text-green-300",
        "ready": "bg-blue-900 text-blue-300",
        "stopped": "bg-gray-700 text-gray-300",
        "error": "bg-red-900 text-red-300",
        "updating": "bg-yellow-900 text-yellow-300",
        "cloning": "bg-yellow-900 text-yellow-300",
        "installing": "bg-yellow-900 text-yellow-300",
        "registered": "bg-gray-700 text-gray-300",
        "success": "bg-green-900 text-green-300",
        "failed": "bg-red-900 text-red-300",
        "timeout": "bg-orange-900 text-orange-300",
        "cancelled": "bg-gray-700 text-gray-300",
        "pending": "bg-yellow-900 text-yellow-300",
        "skipped": "bg-gray-700 text-gray-300",
    }.get(status, "bg-gray-700 text-gray-300")


def tail_file(path: str | Path, lines: int = 100) -> list[str]:
    """Return the last N lines of a file."""
    try:
        with open(path, "r", errors="replace") as f:
            all_lines = f.readlines()
        return [strip_ansi(l.rstrip("\n")) for l in all_lines[-lines:]]
    except (FileNotFoundError, PermissionError):
        return []
