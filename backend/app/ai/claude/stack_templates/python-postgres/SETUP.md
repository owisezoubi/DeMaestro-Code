# Running {{app_name}} locally

> The app runs on port **8100** (backend) and **5273** (frontend) by default,
> chosen so it doesn't conflict with DeMaestro (8000 / 5173). Override via
> `BACKEND_PORT` / `VITE_PORT` in `.env` if you need different ports.

## Option 1 — One-command setup (recommended)

**Mac/Linux:**
```bash
bash setup.sh       # one-time setup (creates venv, installs deps)
bash start.sh       # starts backend + frontend
```

**Windows:**
```batch
setup.bat
start.bat
```

Then open http://localhost:5273 in your browser.

## Option 2 — Docker (no Python or Node needed locally)

```bash
docker compose up --build
```

Open http://localhost:5273.

## Option 3 — Manual setup (if you prefer)

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env         # then edit ../.env
uvicorn app.main:app --reload --port 8100
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Configuration

By default the app uses a local SQLite file (backend/app.db) — no database setup is required, so `bash start.sh` works on any machine. To use PostgreSQL instead, run `docker compose up --build` (Docker provides Postgres automatically) or set DATABASE_URL to your own postgresql:// URL.
