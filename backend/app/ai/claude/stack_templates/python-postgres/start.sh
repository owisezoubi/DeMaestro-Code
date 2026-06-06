#!/usr/bin/env bash
set -e

# Load .env so BACKEND_PORT / VITE_PORT propagate to the subshells.
# Use a line-by-line parser instead of `source` so unquoted values with spaces
# don't break word splitting.
if [ -f ".env" ]; then
  while IFS= read -r raw || [ -n "$raw" ]; do
    # Skip blank lines and comments
    case "$raw" in ''|\#*) continue ;; esac
    # Split on the FIRST =
    key="${raw%%=*}"
    val="${raw#*=}"
    # Strip surrounding double or single quotes if present
    case "$val" in
      \"*\") val="${val%\"}"; val="${val#\"}" ;;
      \'*\') val="${val%\'}"; val="${val#\'}" ;;
    esac
    # Only export if key looks like a valid env var name
    case "$key" in
      [A-Za-z_]*) export "$key=$val" ;;
    esac
  done < .env
fi
BACKEND_PORT="${BACKEND_PORT:-8100}"
VITE_PORT="${VITE_PORT:-5273}"

if [ ! -d "backend/.venv" ]; then
  echo "Backend venv missing. Run ./setup.sh first."; exit 1
fi
if [ ! -d "frontend/node_modules" ]; then
  echo "Frontend node_modules missing. Run ./setup.sh first."; exit 1
fi

trap 'kill 0' SIGINT SIGTERM EXIT

echo "Starting backend on http://localhost:${BACKEND_PORT} ..."
(cd backend && ./.venv/bin/uvicorn app.main:app --reload --port "${BACKEND_PORT}") &

echo "Starting frontend on http://localhost:${VITE_PORT} ..."
(cd frontend && npm run dev -- --port "${VITE_PORT}") &

wait
