# {{app_name}} — Setup Guide

## Quick Start (Docker Compose)

```bash
cp .env.example .env
docker compose up --build
```

The app will be available at:

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000
- **API Docs (Swagger):** http://localhost:8000/docs

## Manual Setup

### Prerequisites

- Python 3.12+
- Node.js 20+
- PostgreSQL 16+

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env   # edit DATABASE_URL to point to your Postgres instance
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend will be available at http://localhost:5173.

## Environment Variables

Copy `.env.example` to `.env` and update:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET_KEY` | Secret for JWT signing — change before deploying |
| `VITE_API_BASE_URL` | URL the frontend uses to reach the backend |
