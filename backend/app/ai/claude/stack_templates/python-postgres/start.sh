#!/usr/bin/env bash
# Start backend and frontend dev servers concurrently.
# Stop with Ctrl+C.

set -e

if [ ! -d "backend/.venv" ]; then
  echo "Backend venv missing. Run ./setup.sh first."
  exit 1
fi
if [ ! -d "frontend/node_modules" ]; then
  echo "Frontend node_modules missing. Run ./setup.sh first."
  exit 1
fi

# Trap so Ctrl+C kills both
trap 'kill 0' SIGINT SIGTERM EXIT

echo "Starting backend on http://localhost:8000 ..."
(cd backend && ./.venv/bin/uvicorn app.main:app --reload --port 8000) &

echo "Starting frontend on http://localhost:5173 ..."
(cd frontend && npm run dev) &

wait
