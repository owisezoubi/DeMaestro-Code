@echo off
setlocal
if not defined BACKEND_PORT set BACKEND_PORT=8100
if not defined VITE_PORT set VITE_PORT=5273
start "backend" cmd /c "cd backend && .venv\Scripts\uvicorn app.main:app --reload --port %BACKEND_PORT%"
start "frontend" cmd /c "cd frontend && npm run dev -- --port %VITE_PORT%"
