#!/bin/bash
# Local development launcher — starts Flask backend + Vite frontend together.
# Usage: ./dev.sh
# Open http://localhost:3000 once both are running.

set -e
cd "$(dirname "$0")"

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

# ── Backend (Flask on port 9000) ─────────────────────────────────────────────
echo ""
echo "▶  Starting Flask backend on http://127.0.0.1:9000 ..."
python -m backend.server > backend.log 2>&1 &
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
open "http://localhost:3000" 2>/dev/null || true

# ── Graceful shutdown on Ctrl+C ──────────────────────────────────────────────
cleanup() {
  echo ""
  echo "Stopping..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  echo "Done."
}
trap cleanup INT TERM

echo "Press Ctrl+C to stop both servers."
wait
