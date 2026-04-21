#!/opt/homebrew/bin/bash
# deerflow-launcher.sh — Launch all DeerFlow services for launchd supervision
# launchd manages this script's lifecycle (KeepAlive); this script manages child services.
# Requires bash 4.3+ for `wait -n` (instant child-exit detection).
#
# Services (startup order):
#   1. LangGraph API  (port 2024) — core agent runtime
#   2. Gateway API    (port 8001) — API proxy
#   3. Frontend       (port 3000) — Next.js UI
#   4. Nginx proxy    (port 2026) — reverse proxy
#
# Shutdown order: reverse (nginx → frontend → gateway → langgraph)
# If any child dies or health check fails, script exits for launchd restart.
set -euo pipefail

export PATH="/Users/brnestrm/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGDIR="$REPO/logs"
mkdir -p "$LOGDIR"

# Source .env for API keys
set -a; source "$REPO/.env" 2>/dev/null; set +a

# ── Service registry (name:port:grace_timeout) ─────────────────────────────
#    Order matters: startup = top→bottom, shutdown = bottom→top
SVC_NAMES=(langgraph gateway frontend nginx)
SVC_PORTS=(2024 8001 3000 2026)
SVC_GRACE=(5 5 2 3)
SVC_PIDS=()

# ── Port conflict guard ─────────────────────────────────────────────────────
kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti ":${port}" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "[launcher] Port ${port} occupied by PID(s) ${pids}, killing..."
    for p in $pids; do
      kill "$p" 2>/dev/null || true
    done
    sleep 1
    for p in $pids; do
      kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null || true
    done
    sleep 3  # ensure OS releases port fully (EADDRINUSE prevention)
  fi
}

for port in "${SVC_PORTS[@]}"; do
  kill_port "$port"
done
nginx -c "$REPO/docker/nginx/nginx.local.conf" -p "$REPO" -s quit 2>/dev/null || true
sleep 1

# ── Graceful kill helper ────────────────────────────────────────────────────
graceful_kill() {
  local pid=$1 timeout=${2:-5} name=${3:-unknown}
  kill "$pid" 2>/dev/null || return 0
  local i=0
  while [ "$i" -lt "$timeout" ]; do
    kill -0 "$pid" 2>/dev/null || { echo "[launcher] ${name} (PID ${pid}) stopped gracefully"; return 0; }
    sleep 1
    ((i++))
  done
  echo "[launcher] ${name} (PID ${pid}) did not stop in ${timeout}s, force killing"
  kill -9 "$pid" 2>/dev/null || true
}

# ── Ordered cleanup (reverse startup order) ─────────────────────────────────
cleanup() {
  echo "[launcher] Shutting down services in reverse order..."
  # Stop health checker first
  [ -n "${HEALTH_PID:-}" ] && kill "$HEALTH_PID" 2>/dev/null || true

  # nginx: graceful quit first
  nginx -c "$REPO/docker/nginx/nginx.local.conf" -p "$REPO" -s quit 2>/dev/null || true

  # Reverse order: nginx(3) → frontend(2) → gateway(1) → langgraph(0)
  local idx=${#SVC_PIDS[@]}
  while [ "$idx" -gt 0 ]; do
    ((idx--))
    local pid="${SVC_PIDS[$idx]:-}"
    [ -z "$pid" ] && continue
    graceful_kill "$pid" "${SVC_GRACE[$idx]}" "${SVC_NAMES[$idx]}"
  done

  # Final sweep: kill entire process group to catch leaked grandchildren
  kill -- -$$ 2>/dev/null || true
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

uv run langgraph dev --port 2024 --no-browser --no-reload \
  >"$LOGDIR/langgraph.log" 2>&1 &
SVC_PIDS+=($!)
wait_for_port 2024 120 "LangGraph" || exit 1

# ── Start Gateway ───────────────────────────────────────────────────────────
PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 \
  >"$LOGDIR/gateway.log" 2>&1 &
SVC_PIDS+=($!)
wait_for_port 8001 60 "Gateway" || exit 1

# ── Start Frontend ──────────────────────────────────────────────────────────
cd "$REPO/frontend"
pnpm run dev >"$LOGDIR/frontend.log" 2>&1 &
SVC_PIDS+=($!)
wait_for_port 3000 120 "Frontend" || exit 1

# ── Start Nginx ─────────────────────────────────────────────────────────────
nginx -g "daemon off;" -c "$REPO/docker/nginx/nginx.local.conf" -p "$REPO" \
  >"$LOGDIR/nginx.log" 2>&1 &
SVC_PIDS+=($!)
wait_for_port 2026 10 "Nginx" || exit 1

echo "[launcher] All 4 services running. Monitoring..."

# ── Background HTTP health checker ──────────────────────────────────────────
health_check_loop() {
  local fail_count=0
  local max_failures=3
  while true; do
    sleep 15
    local all_ok=true
    for i in "${!SVC_NAMES[@]}"; do
      local port="${SVC_PORTS[$i]}"
      local name="${SVC_NAMES[$i]}"
      local url="http://localhost:${port}"
      [ "$port" = "8001" ] && url="http://localhost:8001/health"
      local code
      code=$(curl -s -o /dev/null -w "%{http_code}" "$url" --connect-timeout 3 2>/dev/null || echo "000")
      if [ "$code" = "000" ]; then
        echo "[health] ${name}:${port} unreachable (${code})"
        all_ok=false
      fi
    done
    if [ "$all_ok" = "true" ]; then
      fail_count=0
    else
      ((fail_count++))
      echo "[health] Failure ${fail_count}/${max_failures}"
      if [ "$fail_count" -ge "$max_failures" ]; then
        echo "[health] ${max_failures} consecutive failures, triggering restart"
        kill -TERM $$ 2>/dev/null
        exit 1
      fi
    fi
  done
}
health_check_loop &
HEALTH_PID=$!

# ── Process monitor (wait -n: instant child-exit detection, bash 4.3+) ──────
while true; do
  if ! wait -n "${SVC_PIDS[@]}" 2>/dev/null; then
    # A child exited — identify which one
    for i in "${!SVC_PIDS[@]}"; do
      if ! kill -0 "${SVC_PIDS[$i]}" 2>/dev/null; then
        echo "[launcher] ${SVC_NAMES[$i]} (PID ${SVC_PIDS[$i]}) died. Exiting for launchd restart."
        break
      fi
    done
    exit 1
  fi
done
