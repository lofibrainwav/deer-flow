#!/usr/bin/env bash
# deerflow-launcher.sh — Launch all DeerFlow services for launchd supervision
# launchd manages this script's lifecycle (KeepAlive); this script manages child services.
#
# Services:
#   1. LangGraph API  (port 2024)
#   2. Gateway API    (port 8001)
#   3. Frontend       (port 3000)
#   4. Nginx proxy    (port 2026)
#
# If any child dies, this script exits so launchd can restart everything cleanly.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGDIR="$REPO/logs"
mkdir -p "$LOGDIR"

# Source .env for API keys
set -a; source "$REPO/.env" 2>/dev/null; set +a

# ── Port conflict guard ─────────────────────────────────────────────────────
kill_port() {
  local port=$1
  local pid
  pid=$(lsof -ti ":${port}" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "[launcher] Port ${port} occupied by PID ${pid}, killing..."
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
}

for port in 2024 8001 3000 2026; do
  kill_port "$port"
done

# Also stop nginx gracefully if running
nginx -c "$REPO/docker/nginx/nginx.local.conf" -p "$REPO" -s quit 2>/dev/null || true
sleep 1

# ── Cleanup on exit ─────────────────────────────────────────────────────────
PIDS=()
cleanup() {
  echo "[launcher] Cleaning up child processes..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  nginx -c "$REPO/docker/nginx/nginx.local.conf" -p "$REPO" -s quit 2>/dev/null || true
  sleep 1
  for pid in "${PIDS[@]}"; do
    kill -9 "$pid" 2>/dev/null || true
  done
  echo "[launcher] Cleanup done."
}
trap cleanup EXIT INT TERM

# ── Wait for port helper ────────────────────────────────────────────────────
wait_for_port() {
  local port=$1 timeout=$2 name=$3
  local elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    if lsof -ti ":${port}" >/dev/null 2>&1; then
      echo "[launcher] ${name} ready on port ${port} (${elapsed}s)"
      return 0
    fi
    sleep 1
    ((elapsed++))
  done
  echo "[launcher] ERROR: ${name} failed to start on port ${port} after ${timeout}s"
  return 1
}

# ── Start LangGraph ─────────────────────────────────────────────────────────
cd "$REPO/backend"
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

uv run langgraph dev --port 2024 --no-browser --allow-blocking --no-reload \
  >"$LOGDIR/langgraph.log" 2>&1 &
PIDS+=($!)
wait_for_port 2024 60 "LangGraph" || exit 1

# ── Start Gateway ───────────────────────────────────────────────────────────
PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 \
  >"$LOGDIR/gateway.log" 2>&1 &
PIDS+=($!)
wait_for_port 8001 30 "Gateway" || exit 1

# ── Start Frontend ──────────────────────────────────────────────────────────
cd "$REPO/frontend"
pnpm run dev >"$LOGDIR/frontend.log" 2>&1 &
PIDS+=($!)
wait_for_port 3000 120 "Frontend" || exit 1

# ── Start Nginx ─────────────────────────────────────────────────────────────
nginx -g "daemon off;" -c "$REPO/docker/nginx/nginx.local.conf" -p "$REPO" \
  >"$LOGDIR/nginx.log" 2>&1 &
PIDS+=($!)
wait_for_port 2026 10 "Nginx" || exit 1

echo "[launcher] All 4 services running. Monitoring..."

# ── Monitor: if any child dies, exit so launchd restarts us ─────────────────
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[launcher] Child PID $pid died. Exiting for launchd restart."
      exit 1
    fi
  done
  sleep 5
done
