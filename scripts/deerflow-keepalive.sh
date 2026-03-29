#!/usr/bin/env bash
# deerflow-keepalive.sh — Health-check wrapper for launchd KeepAlive.
# Called by launchd every 30s. If DeerFlow is down, restarts via start-daemon.sh.
# If already healthy, exits immediately (cost: ~100ms).
# COOLDOWN: skip restart if last restart was <60s ago (prevents kill loop).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$REPO_ROOT/logs/keepalive.log"
COOLDOWN_FILE="$REPO_ROOT/logs/.keepalive-last-restart"
mkdir -p "$REPO_ROOT/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

# Quick health check — all 3 core ports must respond
check_port() {
  local url="$1"
  code=$(curl -s -o /dev/null -w "%{http_code}" "$url" --connect-timeout 2 2>/dev/null || echo "000")
  [ "$code" = "200" ] || [ "$code" = "301" ] || [ "$code" = "302" ]
}
healthy=0
check_port "http://localhost:2024" && ((healthy++))
check_port "http://localhost:8001/health" && ((healthy++))
check_port "http://localhost:3000" && ((healthy++))

if [ "$healthy" -ge 3 ]; then
  exit 0
fi

# Cooldown check — don't restart if we restarted less than 60s ago
if [ -f "$COOLDOWN_FILE" ]; then
  last=$(cat "$COOLDOWN_FILE" 2>/dev/null || echo "0")
  now=$(date +%s)
  elapsed=$((now - last))
  if [ "$elapsed" -lt 60 ]; then
    exit 0
  fi
fi

log "WARN: DeerFlow ${healthy}/3 ports healthy — restarting..."
date +%s > "$COOLDOWN_FILE"

cd "$REPO_ROOT"
bash scripts/start-daemon.sh >> "$LOG" 2>&1

log "Restart complete — verifying..."

sleep 2
healthy=0
check_port "http://localhost:2024" && ((healthy++))
check_port "http://localhost:8001/health" && ((healthy++))
check_port "http://localhost:3000" && ((healthy++))

if [ "$healthy" -ge 3 ]; then
  log "OK: DeerFlow restored (${healthy}/3)"
else
  log "ERROR: DeerFlow still unhealthy after restart (${healthy}/3)"
  exit 1
fi
