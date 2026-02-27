#!/usr/bin/env bash
# Quick-start script for the Pomodoro bot.
# Run once on your server / local machine:
#   chmod +x start.sh && ./start.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Check .env ──────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo "[ERROR] .env file not found."
    echo "  Copy .env.example → .env and set BOT_TOKEN=<your token>"
    exit 1
fi

# ── 2. Create venv if missing ─────────────────────────────────────────────────
if [ ! -d venv ]; then
    echo "[INFO] Creating virtual environment…"
    python3 -m venv venv
fi

# ── 3. Install / upgrade dependencies ────────────────────────────────────────
echo "[INFO] Installing dependencies…"
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

# ── 4. Launch bot ─────────────────────────────────────────────────────────────
echo "[INFO] Starting Pomodoro bot…"
exec venv/bin/python bot.py
