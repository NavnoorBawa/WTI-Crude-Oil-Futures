#!/bin/bash
# Local development launcher — starts Flask backend + Vite frontend together.
# Usage: ./dev.sh
# Open http://localhost:3000 once both are running. Ctrl+C stops both; if either server
# exits on its own, the other is stopped too and its log tail is printed.

set -euo pipefail
cd "$(dirname "$0")"

BACKEND_PID=""
FRONTEND_PID=""

# Installed before anything starts, so an early Ctrl+C can never orphan a server.
cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "Stopping..."
  for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
    if [ -n "$pid" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  echo "Done."
}
trap cleanup INT TERM EXIT

# ── Load .env ────────────────────────────────────────────────────────────────
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# API keys come from .env (see .env.example) — nothing is hardcoded here.
if [ -z "${EIA_API_KEY:-}" ]; then
  echo "⚠️  EIA_API_KEY not set — supply-shock playbook will use its cached data."
fi

PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
if [ -z "$PYTHON" ]; then
  echo "No python3/python found on PATH (set PYTHON=/path/to/python)." >&2
  exit 1
fi

# ── Backend (Flask on port 9000) ─────────────────────────────────────────────
echo ""
echo "▶  Starting Flask backend on http://127.0.0.1:9000 ..."
"$PYTHON" -m backend.server > backend.log 2>&1 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID  (logs → backend.log)"

# Give it 3 seconds to bind before the frontend tries to connect.
sleep 3

# ── Frontend (Vite dev server on port 3000) ──────────────────────────────────
echo "▶  Starting Vite frontend on http://localhost:3000 ..."
npm run dev > frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID  (logs → frontend.log)"

sleep 2

# ── Open browser ─────────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────"
echo "  Site:     http://localhost:3000"
echo "  Backend:  http://localhost:9000/data"
echo ""
echo "  First prediction takes ~90s to warm up."
echo "  Prices refresh every 30s, model every 3min."
echo "──────────────────────────────────────────────────"
echo ""
if command -v open >/dev/null 2>&1; then
  open "http://localhost:3000" 2>/dev/null || true          # macOS
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:3000" >/dev/null 2>&1 || true  # Linux desktops
fi

echo "Press Ctrl+C to stop both servers."
# Return as soon as EITHER server exits, instead of waiting on a dead backend forever.
wait -n "$BACKEND_PID" "$FRONTEND_PID" || true
for name in backend frontend; do
  pid_var="$(echo "$name" | tr '[:lower:]' '[:upper:]')_PID"
  if ! kill -0 "${!pid_var}" 2>/dev/null; then
    echo "⚠️  The $name exited. Last lines of $name.log:" >&2
    tail -n 20 "$name.log" >&2 || true
  fi
done
