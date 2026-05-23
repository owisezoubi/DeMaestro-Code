@echo off
REM One-command setup for {{app_name}} (Windows).
REM Run from project root: setup.bat

echo === Setting up {{app_name}} ===

echo.
echo Setting up backend (Python)...
cd backend
if not exist .venv (
  python -m venv .venv
  echo   Created backend\.venv
)
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
echo   Backend deps installed
cd ..

echo.
echo Setting up frontend (Node)...
cd frontend
if not exist node_modules (
  call npm install --silent
  echo   Frontend deps installed
)
cd ..

echo.
if not exist .env (
  copy .env.example .env > nul
  echo Created .env from .env.example - edit it to set your values.
)

echo.
echo === Setup complete ===
echo.
echo Next: run start.bat or start servers manually.
