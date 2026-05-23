#!/usr/bin/env bash
# One-command setup for {{app_name}}.
# Creates Python venv, installs backend deps, installs frontend deps.
# Run from the project root: bash setup.sh

set -e

echo "=== Setting up {{app_name}} ==="

# ----- BACKEND -----
echo ""
echo "Setting up backend (Python)..."
cd backend
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "  Created backend/.venv"
fi
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt --quiet
echo "  Backend deps installed"
cd ..

# ----- FRONTEND -----
echo ""
echo "Setting up frontend (Node)..."
cd frontend
if [ ! -d "node_modules" ]; then
  npm install --silent
  echo "  Frontend deps installed"
else
  echo "  Frontend already installed (skipping)"
fi
cd ..

# ----- ENV -----
echo ""
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example (edit it to set your values)."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next: run ./start.sh to launch both servers, or run them separately:"
echo "  Backend:  cd backend && source .venv/bin/activate && uvicorn app.main:app --reload"
echo "  Frontend: cd frontend && npm run dev"
