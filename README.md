# DeMaestro

Autonomous Multi-Agent System for Full-Stack Web Application Synthesis.

DeMaestro generates a complete, runnable full-stack web application directly from a user's natural-language requirements or a PDF requirements document. The system orchestrates two specialized AI agents through a fully automated pipeline:

- **Gemini** — requirements analyst (structuring, clarification, completeness validation, blueprint generation).
- **Claude (Anthropic SDK)** — code engineer with an architect → generator → tester → debugger → verifier → deployer loop running inside a hardened Docker sandbox.

Capstone Project (Phase B) — Software Engineering Department, Braude College of Engineering.
Authors: Owise Zoubi, Mohamad Atamneh. Advisor: Dr. Natali Levi.

## Features

- **PDF or text input** — paste requirements or upload a PDF; DeMaestro extracts and structures both.
- **Interactive clarification chat** — Gemini asks targeted questions to fill gaps; users answer in a chat interface before the blueprint is locked.
- **Blueprint approval gate** — users review the structured requirements and approve or edit them before any code is generated.
- **Automated code generation pipeline** — Claude runs a multi-stage state machine: `approved → generating → generated → testing → tested → verifying → verified → packaging → ready`.
- **Checklist-driven generation** — the Architect produces a plan + feature checklist; targeted re-generation patches only the files that fail checklist checks.
- **Iterative debug loop** — the Debugger agent auto-fixes import errors, route mismatches, duplicate declarations, missing contract endpoints, and more across multiple cycles.
- **Stop / Restart generation** — users can cancel an in-progress generation and restart it at any time.
- **ZIP export** — the finished app is packaged and available for download from the Project Detail page.
- **Vercel deployment** — one-click deploy to Vercel from the Deployment Panel; the backend uses the Vercel API to provision and push the project automatically.
- **Dark / light theme** — full theme toggle across the entire UI.
- **Active generation banner** — a persistent banner appears on all pages while a generation is running, linking back to the live progress view.

## Quick start

```bash
# 1. Read SETUP_GUIDE.md and complete prerequisites + Firebase setup.

# 2. Configure environment:
cp frontend/.env.example frontend/.env
cp backend/.env.example backend/.env
# Edit both .env files with your Firebase project values, AI provider keys,
# and (optionally) a Vercel token for one-click deployment.
# Place your Firebase service-account JSON at:
#   backend/secrets/firebase-service-account.json

# 3. Install dependencies:
cd frontend && npm install && cd ..
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cd ..

# 4. Run (two terminals):
cd frontend && npm run dev          # http://localhost:5173
cd backend && uvicorn app.main:app --reload   # http://localhost:8000
```

For the full setup walkthrough see [SETUP_GUIDE.md](./SETUP_GUIDE.md).

## User flow

```
Welcome / Login / Signup
        ↓
   New Project  (text or PDF upload)
        ↓
  Clarification Chat  (Gemini asks questions; user answers)
        ↓
  Blueprint Approval  (review structured requirements; edit or approve)
        ↓
  Generation (live progress view with checklist panel; stop/restart available)
        ↓
  Project Detail  (download ZIP, deploy to Vercel, view generated file tree)
```

## Project structure

```
demaestro/
├── frontend/                React + Vite + Tailwind CSS
│   └── src/
│       ├── api/             axios client + per-domain API helpers
│       ├── assets/          logo variants (light/dark)
│       ├── auth/            Firebase client SDK
│       ├── components/      Reusable UI components
│       │   ├── ActiveGenerationBanner.jsx
│       │   ├── DeploymentPanel.jsx
│       │   ├── GeneratedFileTree.jsx
│       │   ├── ProjectOriginsPanel.jsx
│       │   └── ...
│       ├── context/         AuthContext, ThemeContext
│       ├── hooks/           useActiveGeneration, ...
│       ├── lib/             utilities
│       └── pages/
│           ├── Welcome.jsx
│           ├── Login.jsx / Signup.jsx
│           ├── Dashboard.jsx
│           ├── NewProject.jsx
│           ├── ProjectChat.jsx       ← clarification chat
│           ├── ProjectApproval.jsx   ← blueprint review + approval gate
│           ├── ProjectGeneration.jsx ← live generation progress + checklist
│           ├── ProjectDetail.jsx     ← ZIP download, Vercel deploy, file tree
│           ├── ProfilePage.jsx
│           └── AboutPage.jsx
│
├── backend/                 FastAPI + Firebase Admin
│   ├── app/
│   │   ├── main.py          App factory + middleware (CORS, Sentry, slow-request logger)
│   │   ├── config.py        Settings via Pydantic
│   │   ├── auth/            Firebase token verification + request dependencies
│   │   ├── routes/          REST endpoints (auth, projects, requirements, generation,
│   │   │                    structuring, approval)
│   │   ├── controllers/     Business logic per route group
│   │   ├── services/
│   │   │   ├── firestore_service.py   Firestore CRUD
│   │   │   ├── storage_service.py     Cloud Storage (ZIP upload/download)
│   │   │   ├── zip_service.py         ZIP packager
│   │   │   ├── template_service.py    Stack template loader
│   │   │   ├── vercel_deployer.py     Vercel API integration
│   │   │   └── pdf_service.py         PDF text extraction (PyMuPDF)
│   │   ├── ai/
│   │   │   ├── gemini/
│   │   │   │   └── agents/  analyst, clarification, completeness, blueprint,
│   │   │   │                coordinator, revision, summary, validator
│   │   │   └── claude/
│   │   │       └── agents/  architect, generator, tester, debugger,
│   │   │                    verifier, modifier, deployer
│   │   ├── pipeline/
│   │   │   ├── orchestrator.py              Requirements pipeline (Gemini stages)
│   │   │   ├── generation_orchestrator.py   Code generation state machine (Claude stages)
│   │   │   ├── checklist.py                 Checklist schema + coercion
│   │   │   └── checklist_runner.py          Per-item check + targeted regen logic
│   │   ├── sandbox/         Docker container lifecycle
│   │   ├── verification/    Static code checks
│   │   ├── models/          Pydantic schemas (project, user, raw_input, structured_requirements,
│   │   │                    generation_plan)
│   │   └── ai/claude/stack_templates/
│   │       └── python-postgres/   Base template injected into every generated app
│   ├── tests/               pytest (50+ test files)
│   ├── scripts/             Utility scripts (compare_models, replay_debug, prune_snapshots)
│   ├── requirements.txt
│   └── .env.example
│
├── docker-compose.yml       Local dev convenience
├── SETUP_GUIDE.md           Step-by-step setup
└── README.md                This file
```

## Tech stack

| Layer | Tools |
|---|---|
| Frontend | React 18, Vite, Tailwind CSS, TanStack Query v5, axios, React Router v6, React Hook Form, react-markdown, react-dropzone, sonner, lucide-react, firebase v10 |
| Backend | FastAPI, Python 3.11+, Uvicorn, Pydantic v2, firebase-admin, google-generativeai, anthropic SDK, PyMuPDF, docker (Python SDK), psycopg2-binary, structlog, Sentry |
| Storage | Firebase Authentication, Cloud Firestore, Cloud Storage |
| Sandbox | Docker (per-project hardened containers for code execution) |
| Deployment | Vercel (generated apps), Railway / any host (DeMaestro backend) |
| Dev & testing | pytest, pytest-asyncio, httpx, Playwright, ruff, black, ESLint, Prettier |

## API overview

| Prefix | Description |
|---|---|
| `GET /health` | Liveness probe |
| `POST /auth/register` | Register new user in Firestore |
| `GET/POST /projects` | List / create projects |
| `GET/DELETE /projects/:id` | Get / delete a project |
| `POST /projects/:id/requirements` | Submit raw requirements (text or PDF) |
| `POST /projects/:id/requirements/chat` | Send a clarification answer |
| `POST /projects/:id/structure` | Trigger structuring / blueprint generation |
| `GET /projects/:id/approval` | Get blueprint for review |
| `POST /projects/:id/approval` | Approve (or edit and approve) blueprint |
| `POST /projects/:id/generate` | Start generation pipeline |
| `GET /projects/:id/generation/status` | Poll generation progress + checklist state |
| `POST /projects/:id/generation/stop` | Cancel in-progress generation |
| `POST /projects/:id/generation/restart` | Restart generation from scratch |
| `GET /projects/:id/download` | Download generated ZIP |
| `POST /projects/:id/deploy` | Deploy to Vercel |

## Environment variables

See `backend/.env.example` and `frontend/.env.example` for the full list. Key variables:

**Backend**
```
FIREBASE_SERVICE_ACCOUNT_PATH=secrets/firebase-service-account.json
FIREBASE_STORAGE_BUCKET=<your-bucket>.appspot.com
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
VERCEL_TOKEN=...            # optional — only needed for one-click deploy
SENTRY_DSN=...              # optional
```

**Frontend**
```
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=...
VITE_FIREBASE_PROJECT_ID=...
VITE_FIREBASE_STORAGE_BUCKET=...
VITE_FIREBASE_MESSAGING_SENDER_ID=...
VITE_FIREBASE_APP_ID=...
VITE_API_BASE_URL=http://localhost:8000
```

## Running tests

```bash
cd backend
source .venv/bin/activate
pytest
```

## Documents

- [SETUP_GUIDE.md](./SETUP_GUIDE.md) — prerequisites, Firebase setup, GitHub, and first run.

## License

Academic project — not licensed for redistribution at this stage.
