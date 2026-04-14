#!/usr/bin/env bash
# PyRunner installation script for Ubuntu Desktop 24.04+
set -euo pipefail

PYRUNNER_ROOT="${PYRUNNER_ROOT:-$HOME/pyrunner}"
DASHBOARD_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=============================================="
echo "  PyRunner Installer"
echo "  Dashboard source: $DASHBOARD_SRC"
echo "  Data root:        $PYRUNNER_ROOT"
echo "=============================================="
echo

# ── Check prerequisites ─────────────────────────────────────────────
echo "[1/7] Checking prerequisites..."

check_cmd() {
  command -v "$1" &>/dev/null || { echo "ERROR: '$1' not found. $2"; exit 1; }
}

check_cmd git    "Install with: sudo apt install git"
check_cmd uv     "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
check_cmd python3 "Install with: sudo apt install python3"
check_cmd supervisord "Install with: sudo apt install supervisor"

echo "  All prerequisites found."

# ── System packages ──────────────────────────────────────────────────
echo "[2/7] Installing optional system packages..."
sudo apt-get install -y --no-install-recommends xvfb curl 2>/dev/null || \
  echo "  (apt install skipped — run manually if needed)"

# ── Directory structure ──────────────────────────────────────────────
echo "[3/7] Creating directory structure..."
mkdir -p \
  "$PYRUNNER_ROOT/projects" \
  "$PYRUNNER_ROOT/supervisor/conf.d" \
  "$PYRUNNER_ROOT/logs" \
  "$PYRUNNER_ROOT/data"

echo "  Directories created under $PYRUNNER_ROOT"

# ── Copy / link dashboard source ────────────────────────────────────
echo "[4/7] Setting up dashboard..."
if [ "$DASHBOARD_SRC" != "$PYRUNNER_ROOT/dashboard" ]; then
  if [ -L "$PYRUNNER_ROOT/dashboard" ]; then
    rm "$PYRUNNER_ROOT/dashboard"
  fi
  ln -sfn "$DASHBOARD_SRC" "$PYRUNNER_ROOT/dashboard"
  echo "  Symlinked $DASHBOARD_SRC -> $PYRUNNER_ROOT/dashboard"
fi

# ── Install Python dependencies ──────────────────────────────────────
echo "[5/7] Installing Python dependencies with uv..."
cd "$DASHBOARD_SRC"
uv sync
echo "  Dependencies installed."

# ── Copy default config ──────────────────────────────────────────────
echo "[6/7] Setting up config..."
CONFIG_PATH="$PYRUNNER_ROOT/config.toml"
if [ ! -f "$CONFIG_PATH" ]; then
  cp "$DASHBOARD_SRC/config.toml" "$CONFIG_PATH"
  echo "  Config created at $CONFIG_PATH"
else
  echo "  Config already exists at $CONFIG_PATH (skipping)"
fi

# ── Supervisord include ──────────────────────────────────────────────
SUPERVISOR_CONF="/etc/supervisor/supervisord.conf"
INCLUDE_LINE="files = $PYRUNNER_ROOT/supervisor/conf.d/*.conf"
if [ -f "$SUPERVISOR_CONF" ]; then
  if ! grep -qF "$PYRUNNER_ROOT/supervisor/conf.d" "$SUPERVISOR_CONF"; then
    echo "  Adding PyRunner conf.d to supervisord.conf..."
    sudo bash -c "cat >> $SUPERVISOR_CONF" <<EOF

; PyRunner managed configs
[include]
$INCLUDE_LINE
EOF
    sudo supervisorctl reread 2>/dev/null || true
    sudo supervisorctl update 2>/dev/null || true
  else
    echo "  Supervisord include already configured."
  fi
else
  echo "  WARNING: $SUPERVISOR_CONF not found. Add manually:"
  echo "    [include]"
  echo "    $INCLUDE_LINE"
fi

# ── Systemd user service ─────────────────────────────────────────────
echo "[7/7] Creating systemd user service..."
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/pyrunner.service" <<EOF
[Unit]
Description=PyRunner Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$DASHBOARD_SRC
ExecStart=$(which uv) run uvicorn dashboard.main:app --host 0.0.0.0 --port 8420
Restart=on-failure
RestartSec=5
Environment=PYRUNNER_ROOT=$PYRUNNER_ROOT
Environment=PYRUNNER_CONFIG=$CONFIG_PATH

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable pyrunner.service

echo "  Service created: $SYSTEMD_DIR/pyrunner.service"
echo

echo "=============================================="
echo "  Installation complete!"
echo ""
echo "  Start PyRunner:"
echo "    systemctl --user start pyrunner"
echo ""
echo "  Access dashboard:"
echo "    http://localhost:8420"
echo ""
echo "  View logs:"
echo "    journalctl --user -u pyrunner -f"
echo "=============================================="
