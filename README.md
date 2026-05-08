# DeMaestro

Autonomous Multi-Agent System for Full-Stack Web Application Synthesis.

DeMaestro generates a complete and runnable full-stack web application directly from a user's natural-language requirements. The system uses two specialized AI agents:

- **Gemini** — requirements analyst (structuring, clarification, summary, blueprint).
- **Claude (Agent SDK)** — code engineer with iterative test/debug loop in a hardened Docker sandbox.

Capstone Project (Phase B) — Software Engineering Department, Braude College of Engineering.
Authors: Owise Zoubi, Mohamad Atamneh. Advisor: Dr. Natali Levi.

## Quick start

```bash
# 1. Read SETUP_GUIDE.md and complete prerequisites + Firebase setup.
# 2. Configure environment:
cp frontend/.env.example frontend/.env
cp backend/.env.example backend/.env
# Edit both .env files with values from your Firebase project + AI provider keys.
# Place Firebase service-account JSON at backend/secrets/firebase-service-account.json

# 3. Install:
cd frontend && npm install && cd ..
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cd ..

# 4. Run (two terminals):
cd frontend && npm run dev          # http://localhost:5173
cd backend && uvicorn app.main:app --reload   # http://localhost:8000
```

For the full setup walkthrough see [SETUP_GUIDE.md](./SETUP_GUIDE.md).

## Project structure

```
demaestro/
├── frontend/                React + Vite + Tailwind + shadcn/ui
│   └── src/
│       ├── auth/            Firebase client SDK + AuthContext
│       ├── api/             axios client with token interceptor
│       ├── pages/           Login, Signup, Dashboard, NewProject, ...
│       ├── components/      Reusable UI (shadcn-style)
│       └── context/         React Context providers
│
├── backend/                 FastAPI + Firebase Admin
│   ├── app/
│   │   ├── main.py          App factory
│   │   ├── config.py        Settings via Pydantic
│   │   ├── auth/            Firebase token verification
│   │   ├── routes/          API routes (auth, projects, requirements, generation)
│   │   ├── controllers/     Business logic per route group
│   │   ├── services/        Firestore, Storage, ZIP packager
│   │   ├── ai/
│   │   │   ├── gemini/      Requirements analyst agent
│   │   │   └── claude/      Code engineer agent (Agent SDK)
│   │   ├── sandbox/         Docker container lifecycle
│   │   ├── verification/    Static checks
│   │   ├── pipeline/        Orchestrator (background thread)
│   │   └── models/          Pydantic schemas
│   ├── tests/               pytest
│   ├── requirements.txt
│   └── .env.example
│
├── docker-compose.yml       Local dev convenience
├── BOOK_CHANGES.md          Phase A → B delta log
├── SETUP_GUIDE.md           Step-by-step setup
├── AI_PROMPT.md             Prompt to regenerate skeleton via another AI
└── README.md                This file
```

## Tech stack

| Layer | Tools |
|---|---|
| Frontend | React 18, Vite, Tailwind CSS, shadcn/ui, TanStack Query, axios, React Router, React Hook Form, react-markdown, react-dropzone, sonner, firebase v9, Sentry |
| Backend | FastAPI, Python 3.11+, Uvicorn, Pydantic v2, firebase-admin, google-generativeai, claude-agent-sdk, PyMuPDF, docker (Python SDK), structlog, pytest |
| Storage | Firebase Authentication, Cloud Firestore, Cloud Storage |
| Sandbox | Docker (per-project hardened containers) |
| Dev | Git, GitHub, VS Code |

## Documents

- [SETUP_GUIDE.md](./SETUP_GUIDE.md) — install prerequisites, set up Firebase, GitHub, and run the project for the first time.
- [BOOK_CHANGES.md](./BOOK_CHANGES.md) — running list of Phase B decisions that change Phase A.
- [AI_PROMPT.md](./AI_PROMPT.md) — self-contained prompt to regenerate this skeleton via another AI tool.

## Status

This repository is the **Week 1 skeleton**: working login/signup, empty dashboard, FastAPI app shell with Firebase token verification. No AI logic yet. Subsequent weeks add chat input + PDF upload, the Gemini requirements pipeline, the approval gate, the Claude generation loop, and the ZIP export.

## License

Academic project — not licensed for redistribution at this stage.
