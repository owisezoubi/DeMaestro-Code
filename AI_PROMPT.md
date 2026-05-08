# AI Regeneration Prompt

This file is a **self-contained prompt** you can paste into ChatGPT, Claude, Gemini, Cursor, or any LLM-driven coding tool to regenerate the same DeMaestro skeleton from scratch — useful as a backup, for comparison, or to test how different tools interpret the same spec.

Everything below the `===== PROMPT BEGINS =====` line is the prompt itself. Copy from there to the end and paste into your tool of choice. (No Markdown formatting needs to be stripped — most coding agents accept Markdown.)

---

## Tips for using the prompt

- **Cursor / Aider / Cline:** open an empty folder, paste the prompt as your first message, and let the agent create files iteratively.
- **ChatGPT / Claude.ai:** paste the prompt and the assistant will produce a single ZIP / list of file contents. Copy each file manually, or ask for a follow-up "Generate file X in full" if any are abbreviated.
- **Gemini in AI Studio:** same as ChatGPT.
- **If the tool truncates output:** ask "Continue with the remaining files" until all files in the structure tree are produced.

---

## ===== PROMPT BEGINS =====

You are a senior full-stack engineer. Generate a complete starter repository for **DeMaestro**, a capstone project that will become an autonomous multi-agent system for full-stack web app generation. **At this stage you are only generating the Week-1 skeleton: the foundation that compiles and runs, without any AI logic yet.**

The project is a **monorepo** with two top-level apps: a React frontend and a FastAPI backend, plus shared root files.

### Hard requirements

- **Do not** include AI-generation logic, Gemini calls, Claude calls, or sandbox execution code. Those are stubbed for later weeks.
- Every file must be production-quality and runnable.
- Use exactly the dependency versions listed below.
- Frontend uses **JavaScript (JSX)**, not TypeScript.
- Code must follow the folder structure shown verbatim.
- Use clean, idiomatic patterns; no AI boilerplate comments like "// TODO add your logic here" except where I explicitly mark a stub.

### Tech stack

**Frontend**
- React 18 + Vite 5
- Tailwind CSS 3.4 (utility-first, with a small set of @layer component classes for `btn`, `input`, `card`)
- React Router v6 for navigation
- TanStack Query v5 for server state and polling
- axios with a request interceptor that attaches a Firebase ID token automatically
- Firebase v10 modular client SDK (Auth, Storage)
- React Hook Form, sonner (toasts), lucide-react (icons), react-markdown + remark-gfm, react-dropzone, clsx, tailwind-merge
- ESLint + Prettier configs

**Backend**
- FastAPI 0.115 + Uvicorn 0.30 (async)
- Pydantic v2 + pydantic-settings for typed configuration loaded from `.env`
- firebase-admin 6.5 for server-side auth + Firestore + Cloud Storage
- structlog for JSON-structured logging
- Sentry SDK (optional, gated by env var)
- Stubs for: google-generativeai, claude-agent-sdk, PyMuPDF, docker (Python SDK)
- pytest + httpx + ruff + black

**Storage**
- Firebase Authentication for users
- Cloud Firestore for project data
- Cloud Storage for PDF uploads + ZIP exports

### Folder structure (create exactly this tree)

```
demaestro/
├── README.md
├── SETUP_GUIDE.md          (placeholder — write a 1-line note that says "See full setup guide")
├── BOOK_CHANGES.md         (placeholder — same)
├── docker-compose.yml
├── .gitignore
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── index.html
│   ├── jsconfig.json
│   ├── .eslintrc.json
│   ├── .prettierrc.json
│   ├── .env.example
│   ├── .gitignore
│   ├── README.md
│   ├── public/vite.svg
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── index.css
│       ├── auth/firebase.js
│       ├── context/AuthContext.jsx
│       ├── api/client.js
│       ├── api/projects.js
│       ├── components/Logo.jsx
│       ├── components/ProtectedRoute.jsx
│       ├── pages/Login.jsx
│       ├── pages/Signup.jsx
│       └── pages/Dashboard.jsx
└── backend/
    ├── requirements.txt
    ├── Dockerfile
    ├── .env.example
    ├── README.md
    ├── secrets/.gitkeep    (with a placeholder note about service-account JSON)
    ├── app/
    │   ├── __init__.py
    │   ├── main.py
    │   ├── config.py
    │   ├── auth/
    │   │   ├── __init__.py
    │   │   ├── firebase_admin.py
    │   │   └── dependencies.py
    │   ├── routes/
    │   │   ├── __init__.py
    │   │   ├── auth.py
    │   │   ├── projects.py
    │   │   ├── requirements.py
    │   │   └── generation.py
    │   ├── services/
    │   │   ├── __init__.py
    │   │   ├── firestore_service.py
    │   │   ├── storage_service.py
    │   │   └── zip_service.py
    │   ├── ai/
    │   │   ├── __init__.py
    │   │   ├── gemini/__init__.py
    │   │   ├── gemini/client.py
    │   │   ├── claude/__init__.py
    │   │   └── claude/agent.py
    │   ├── sandbox/
    │   │   ├── __init__.py
    │   │   └── docker_runner.py
    │   ├── verification/
    │   │   ├── __init__.py
    │   │   └── checks.py
    │   ├── pipeline/
    │   │   ├── __init__.py
    │   │   └── orchestrator.py
    │   ├── controllers/__init__.py
    │   └── models/
    │       ├── __init__.py
    │       ├── user.py
    │       └── project.py
    └── tests/
        ├── __init__.py
        └── test_health.py
```

### Behavioral spec

**Frontend behavior**

1. `/login` and `/signup` use Firebase Email/Password auth via the modular SDK.
2. `/dashboard` is protected by a `<ProtectedRoute>` wrapper that redirects to `/login` if `useAuth().user` is null. While Firebase auth is still resolving, render a "Loading…" message.
3. The Dashboard:
   - Calls `GET /auth/me` once on mount to ensure the user document exists in Firestore (handled server-side).
   - Uses TanStack Query to call `GET /projects` and renders a grid of project cards.
   - Has a "New project" button that prompts for a name (`window.prompt`) and calls `POST /projects` to create one. Refetches on success.
   - Each project card shows the name, ID, and a colored status pill mapped from the `status` enum.
   - Empty state when no projects exist.
4. axios interceptor attaches `Authorization: Bearer <firebase-id-token>` to every backend request.
5. `index.css` defines `@layer components` for `.btn`, `.btn-primary`, `.btn-secondary`, `.input`, `.card`.
6. Tailwind config defines a `primary` color palette (50–800) seeded around `#1F3A68` (deep navy) and an `accent` color around `#D97706` (amber).
7. Use Inter font loaded from Google Fonts.

**Backend behavior**

1. FastAPI app in `app/main.py` configures CORS from `CORS_ORIGINS` env var (comma-separated list), JSON-structured logging via structlog, optional Sentry init.
2. Lifespan event calls `init_firebase()` from `app.auth.firebase_admin`. If the service-account JSON is missing, log a warning and continue (so `/health` still works in dev). Endpoints that need Firebase fail with a clear error when called.
3. `app/config.py` uses `pydantic-settings` to read `.env`. Every key from the `.env.example` should map to a typed field.
4. `app/auth/dependencies.py` exposes `get_current_user` as a FastAPI dependency that:
   - Reads the `Authorization` header.
   - Strips `Bearer `.
   - Calls `firebase_admin.auth.verify_id_token`.
   - Returns an `AuthUser` Pydantic model `{uid, email, email_verified, name}`.
   - Raises `401` on failure.
   Expose `CurrentUser = Annotated[AuthUser, Depends(get_current_user)]` for cleaner route signatures.
5. Routes:
   - `GET  /health` — returns `{"status":"ok","env":<env>}`. No auth.
   - `GET  /auth/me` — returns the user; ensures `users/{uid}` doc exists, updates `last_login_at`.
   - `GET  /projects` — list projects for current user.
   - `POST /projects` — body `{name}`, creates project at `users/{uid}/projects/{id}` with `status="awaiting_input"`. Generate a 12-char hex `id`.
   - `GET  /projects/{id}` — fetch one. 404 if not found.
   - `DELETE /projects/{id}` — delete the project doc.
   - `POST /projects/{id}/requirements/text` and `/requirements/pdf` — return 501 with detail "Not implemented yet — Week 2".
   - `GET /projects/{id}/status` — returns `{"project_id","status":"awaiting_input"}` (placeholder).
   - `POST /projects/{id}/approve`, `POST /projects/{id}/generate`, `GET /projects/{id}/download` — all return 501 with appropriate week label.
6. Firestore service module uses this layout:
   ```
   users/{uid}                                user metadata
   └── projects/{projectId}                   project doc
         (subcollections for raw_inputs, structured_requirements,
          clarifications, summary_documents, blueprints, ai_calls,
          verification_logs, exports — created in later weeks)
   ```
7. Pydantic models:
   - `AuthUser` (in `models/user.py`): `uid`, `email` (EmailStr optional), `email_verified`, `name`.
   - `UserProfile` (in `models/user.py`): full user doc shape.
   - `ProjectStatus` enum with values:
     `awaiting_input, structuring, clarifying, awaiting_approval, blueprinting, generating, verifying, packaging, ready, failed`.
   - `StackChoice` enum: `python-sqlite, python-postgres, node-mongo`.
   - `ProjectCreate` (input) `{name}`, `ProjectMeta` (output) `{id, name, status, stack_choice?, created_at?, updated_at?}`.
8. AI module stubs (`ai/gemini/client.py`, `ai/claude/agent.py`):
   - Have a `is_mock()` helper backed by `MOCK_AI` env var.
   - In mock mode, return canned dummy values.
   - Otherwise raise `NotImplementedError("Implemented in Week N")`.
9. Sandbox stub (`sandbox/docker_runner.py`):
   - Define `DEFAULT_FLAGS = dict(network_mode="none", mem_limit="512m", nano_cpus=int(0.5*1e9), detach=True)`.
   - `launch(image, project_dir)` raises `NotImplementedError`.
10. Verification stub: `run_all(project_dir)` raises `NotImplementedError`.
11. Pipeline orchestrator stub: `kick_off(uid, project_id)` logs and raises `NotImplementedError`.
12. Test: `tests/test_health.py` uses `fastapi.testclient.TestClient` to assert `GET /health` returns 200 with `{"status":"ok"}`.

### Environment variables

**`frontend/.env.example`:**
```
VITE_FIREBASE_API_KEY=
VITE_FIREBASE_AUTH_DOMAIN=demaestro.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=demaestro
VITE_FIREBASE_STORAGE_BUCKET=demaestro.appspot.com
VITE_FIREBASE_MESSAGING_SENDER_ID=
VITE_FIREBASE_APP_ID=
VITE_API_BASE_URL=http://localhost:8000
VITE_SENTRY_DSN=
```

**`backend/.env.example`:**
```
FIREBASE_SERVICE_ACCOUNT_PATH=secrets/firebase-service-account.json
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-2.5-pro
ANTHROPIC_API_KEY=sk-ant-your-key-here
CLAUDE_MODEL=claude-sonnet-4-6
MOCK_AI=0
SENTRY_DSN=
ENV=development
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

### Dependency versions (use exactly)

**Frontend (`package.json`):**
```
react ^18.3.1
react-dom ^18.3.1
react-router-dom ^6.26.2
@tanstack/react-query ^5.59.0
axios ^1.7.7
firebase ^10.13.2
react-hook-form ^7.53.0
react-markdown ^9.0.1
remark-gfm ^4.0.0
react-dropzone ^14.2.9
sonner ^1.5.0
lucide-react ^0.446.0
clsx ^2.1.1
tailwind-merge ^2.5.2

vite ^5.4.8
@vitejs/plugin-react ^4.3.1
tailwindcss ^3.4.13
postcss ^8.4.47
autoprefixer ^10.4.20
eslint ^9.11.1
eslint-plugin-react ^7.36.1
eslint-plugin-react-hooks ^4.6.2
eslint-plugin-react-refresh ^0.4.12
prettier ^3.3.3
```

**Backend (`requirements.txt`):**
```
fastapi==0.115.0
uvicorn[standard]==0.30.6
python-multipart==0.0.9
pydantic==2.9.2
pydantic-settings==2.5.2
python-dotenv==1.0.1
firebase-admin==6.5.0
google-generativeai==0.8.3
claude-agent-sdk==0.1.0
PyMuPDF==1.24.10
docker==7.1.0
structlog==24.4.0
sentry-sdk[fastapi]==2.14.0
pytest==8.3.3
pytest-asyncio==0.24.0
httpx==0.27.2
ruff==0.6.8
black==24.10.0
```

### docker-compose.yml

Two services (`backend`, `frontend`):
- `backend`: build from `./backend/Dockerfile`, port 8000, mount `./backend:/app` and `./backend/secrets:/app/secrets:ro` and `/var/run/docker.sock:/var/run/docker.sock` (the last one is needed in later weeks for spawning sandbox containers; comment that in the file).
- `frontend`: `node:20-alpine`, port 5173, mount `./frontend:/app`, command runs `npm install && npm run dev -- --host 0.0.0.0`.
- Use `env_file:` to point each service at its own `.env`.

### Final delivery

When you finish, list every file in the tree and confirm that:

- `cd backend && uvicorn app.main:app --reload` starts and `/health` returns 200.
- `cd frontend && npm install && npm run dev` starts on port 5173 and the login page renders.
- Sign-up succeeds when `frontend/.env` is filled with a real Firebase Web App config.
- After signup, the dashboard's call to `GET /auth/me` should succeed (assuming `backend/secrets/firebase-service-account.json` is in place) and return the authenticated user.

Generate every file in full. Do not abbreviate, do not say "// rest unchanged" or "// see above" anywhere.

## ===== PROMPT ENDS =====
