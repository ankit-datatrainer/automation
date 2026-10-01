#!/usr/bin/env bash
# ==========================================================
# VFS Global Automation — VPS Production Runner with Xvfb
# ==========================================================
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

# Activate Virtual Environment
if [ -d "$DIR/venv" ]; then
    source "$DIR/venv/bin/activate"
fi

export PORT=4140
export HOST="0.0.0.0"
export PYTHONUNBUFFERED="1"

if command -v xvfb-run >/dev/null 2>&1; then
    echo "[VFS VPS RUNNER] Starting with xvfb-run virtual display (1440x900x24)..."
    exec xvfb-run --auto-servernum --server-args="-screen 0 1440x900x24" python3 app.py
else
    echo "[VFS VPS RUNNER] xvfb-run not detected. Running directly with python3..."
    exec python3 app.py
fi
