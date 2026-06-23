#!/usr/bin/env bash
set -e

# Load .env so BACKEND_PORT / VITE_PORT propagate to subshells.
# Line-by-line parser handles unquoted values with spaces correctly.
if [ -f ".env" ]; then
  while IFS= read -r raw || [ -n "$raw" ]; do
    case "$raw" in ''|\#*) continue ;; esac
    key="${raw%%=*}"
    val="${raw#*=}"
    case "$val" in
      \"*\") val="${val%\"}"; val="${val#\"}" ;;
      \'*\') val="${val%\'}"; val="${val#\'}" ;;
    esac
    case "$key" in
      [A-Za-z_]*) export "$key=$val" ;;
    esac
  done < .env
fi

BACKEND_PORT="${BACKEND_PORT:-8100}"
VITE_PORT="${VITE_PORT:-5273}"

# Auto-detect free backend port — avoids collisions when multiple generated
# apps run simultaneously (second app gets 8101, third gets 8102, etc.).
while lsof -iTCP:"$BACKEND_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; do
  BACKEND_PORT=$((BACKEND_PORT + 1))
done
export BACKEND_PORT

if [ ! -d "backend/.venv" ]; then
  echo "Backend venv missing. Run ./setup.sh first."; exit 1
fi
if [ ! -d "frontend/node_modules" ]; then
  echo "Frontend node_modules missing. Run ./setup.sh first."; exit 1
fi

trap 'kill 0' SIGINT SIGTERM EXIT

echo "Starting backend  on http://localhost:${BACKEND_PORT} ..."
echo "Starting frontend on http://localhost:${VITE_PORT}    ..."
echo "  (frontend /api/* → proxied to backend port ${BACKEND_PORT})"

(cd backend && ./.venv/bin/uvicorn app.main:app --reload --port "${BACKEND_PORT}") &

# Pass the resolved BACKEND_PORT so Vite's proxy target is always correct.
(cd frontend && BACKEND_PORT="${BACKEND_PORT}" npm run dev -- --port "${VITE_PORT}") &

wait
