#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Pulling latest changes..."
git pull

echo "Syncing dependencies..."
uv sync

echo "Restarting pyrunner..."
systemctl --user restart pyrunner

echo "Done. Status:"
systemctl --user status pyrunner --no-pager -l
