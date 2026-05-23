# Running {{app_name}} locally

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

Then open http://localhost:5173 in your browser.

## Option 2 — Docker (no Python or Node needed locally)

```bash
docker compose up --build
```

Open http://localhost:5173.

## Option 3 — Manual setup (if you prefer)

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env         # then edit ../.env
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Configuration

Edit `.env` at the project root. The defaults work for local Docker Compose; for manual setup, you'll need to point `DATABASE_URL` at a running Postgres instance (or use Docker for just the database: `docker compose up db`).
