@echo off
if not exist backend\.venv ( echo Run setup.bat first. & exit /b 1 )
if not exist frontend\node_modules ( echo Run setup.bat first. & exit /b 1 )
start "Backend"  cmd /k "cd backend && .venv\Scripts\uvicorn.exe app.main:app --reload --port 8000"
start "Frontend" cmd /k "cd frontend && npm run dev"
