#!/usr/bin/env bash
# deerflow-keepalive.sh — Keep LangGraph alive (direct start, no daemon wrapper)
set -euo pipefail

LOG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/keepalive.log"
COOLDOWN_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/.keepalive-last-restart"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

# Check LangGraph only (core service)
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:2024 --connect-timeout 2 2>/dev/null || echo "000")
if [ "$code" = "200" ]; then
  exit 0
fi

# Cooldown — skip if restarted <90s ago
if [ -f "$COOLDOWN_FILE" ]; then
  last=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo "0")
  elapsed=$(( $(date +%s) - last ))
  [ "$elapsed" -lt 90 ] && exit 0
fi

log "WARN: LangGraph down — starting directly..."
date +%s > "$COOLDOWN_FILE"

# Source .env for API keys
set -a; source "$REPO/.env" 2>/dev/null; set +a

cd "$REPO/backend"
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
nohup uv run langgraph dev --port 2024 --no-browser --no-reload > ../logs/langgraph.log 2>&1 &

sleep 8
code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:2024 --connect-timeout 2 2>/dev/null || echo "000")
if [ "$code" = "200" ]; then
  log "OK: LangGraph restored"
else
  log "ERROR: LangGraph still down after restart"
  exit 1
fi
