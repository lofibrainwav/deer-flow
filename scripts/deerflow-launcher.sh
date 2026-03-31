#!/usr/bin/env bash
# deerflow-launcher.sh — Launch LangGraph as foreground process for launchd
# launchd manages lifecycle (KeepAlive), this script only does setup + exec
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Source .env for API keys
set -a; source "$REPO/.env" 2>/dev/null; set +a

cd "$REPO/backend"
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# exec replaces this shell — launchd manages the resulting process directly
exec uv run langgraph dev --port 2024 --no-browser --allow-blocking --no-reload
