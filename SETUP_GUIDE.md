# DeMaestro — Setup Guide

This guide walks you through every prerequisite, the Firebase project setup, the GitHub repository setup, and the first run of the skeleton project. Follow these steps in order. After completing them you will have a working development environment with the login screen reachable at `http://localhost:5173`.

---

## 1. Prerequisites

You need five tools installed: **Node.js**, **Python 3.11+**, **Docker Desktop**, **Git**, and **VS Code**. Below are install/update instructions per platform. If you already have a tool, jump to the *update* command for that tool to make sure it is current.

### 1.1 Node.js (v20 or newer) and npm

DeMaestro uses Vite + React on the frontend, which requires Node 20+.

**Check your current version:**
```bash
node -v
npm -v
```

**macOS** — install or update via Homebrew:
```bash
brew install node@20
brew upgrade node@20    # if already installed
```

**Windows** — download the LTS installer from <https://nodejs.org> (pick "LTS" v20+), run it, accept defaults. Or via winget:
```bash
winget install OpenJS.NodeJS.LTS
```

**Linux (Ubuntu/Debian)**:
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

After install, verify:
```bash
node -v   # should print v20.x.x or higher
npm -v
```

### 1.2 Python 3.11 or newer

DeMaestro's backend runs on FastAPI which requires Python 3.11+.

**Check current version:**
```bash
python3 --version
```

**macOS** — Homebrew:
```bash
brew install python@3.11
brew upgrade python@3.11
```

**Windows** — download from <https://www.python.org/downloads/> (pick 3.11.x or newer), run installer, **make sure to tick "Add Python to PATH"**. Or via winget:
```bash
winget install Python.Python.3.11
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip
```

Verify:
```bash
python3 --version   # should print 3.11.x or higher
pip3 --version
```

### 1.3 Docker Desktop

Used to run the per-project sandbox containers later in the project, plus the optional Postgres/Mongo containers for generated apps.

- **macOS / Windows** — install from <https://www.docker.com/products/docker-desktop/>. Run the installer, sign in, accept defaults.
- **Linux** — install Docker Engine following <https://docs.docker.com/engine/install/>.

After installing, **start Docker Desktop** and verify:
```bash
docker --version
docker info
```

If `docker info` errors with "Cannot connect to the Docker daemon," Docker Desktop isn't running — start it before continuing.

### 1.4 Git

**macOS** — Git is preinstalled with Xcode Command Line Tools:
```bash
xcode-select --install
git --version
```

**Windows** — install from <https://git-scm.com/download/win> or:
```bash
winget install Git.Git
```

**Linux**:
```bash
sudo apt install -y git
```

Configure your identity once (used for commits):
```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

### 1.5 Visual Studio Code

Download from <https://code.visualstudio.com/>. Recommended extensions for this project:

- **ESLint** (`dbaeumer.vscode-eslint`) — JavaScript linting
- **Prettier** (`esbenp.prettier-vscode`) — formatting
- **Tailwind CSS IntelliSense** (`bradlc.vscode-tailwindcss`)
- **Python** (`ms-python.python`)
- **Pylance** (`ms-python.vscode-pylance`)
- **Ruff** (`charliermarsh.ruff`) — Python linting/formatting
- **Docker** (`ms-azuretools.vscode-docker`)
- **GitLens** (`eamodio.gitlens`) — better git UX

Install all of them at once from the Extensions panel (Cmd/Ctrl+Shift+X) by searching the IDs above.

---

## 2. Firebase project setup

DeMaestro uses three Firebase services: **Authentication** (user accounts), **Cloud Firestore** (project metadata), and **Cloud Storage** (PDF uploads + ZIP exports). All three live in a single Firebase project.

### 2.1 Create the project

1. Go to <https://console.firebase.google.com> and sign in with a Google account.
2. Click **"Add project"** (or "Create a project").
3. Project name: `demaestro` (or anything you like — the displayed name is for you only).
4. Disable Google Analytics (not needed). Click **Continue → Create project**. Wait ~30 seconds.
5. Once the project is ready, click **Continue** to enter the project console.

### 2.2 Enable Authentication

1. In the left sidebar of the Firebase console, click **Build → Authentication**.
2. Click **Get started**.
3. Under "Sign-in method," click **Email/Password**, toggle **Enable**, click **Save**.
4. Optional but recommended: also enable **Google** sign-in (one-click signup is much smoother). Toggle **Enable**, set a project support email, click **Save**.

### 2.3 Enable Cloud Firestore

1. In the sidebar, click **Build → Firestore Database**.
2. Click **Create database**.
3. Pick a location near you (e.g., `eur3 (europe-west)` for users in Israel/Europe). **This cannot be changed later.**
4. Start in **production mode** (we will publish proper security rules below).
5. Click **Enable**. Wait ~30 seconds.

After it's created, go to the **Rules** tab and paste these rules, then click **Publish**:

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Users can only read/write their own user doc and everything under it.
    match /users/{uid}/{document=**} {
      allow read, write: if request.auth != null && request.auth.uid == uid;
    }
  }
}
```

These rules implement NFR8 ("users only access their own projects") with a single rule.

### 2.4 Enable Cloud Storage

1. Sidebar → **Build → Storage**.
2. Click **Get started**.
3. Pick the same location as Firestore. Start in **production mode**. Click **Done**.

After created, go to the **Rules** tab and paste:

```
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /users/{uid}/{allPaths=**} {
      allow read, write: if request.auth != null && request.auth.uid == uid;
    }
  }
}
```

Click **Publish**.

### 2.5 Get the frontend (Web App) config

The React frontend talks to Firebase using a **public** config (API key, project ID, etc. — these are not secrets, they identify the project to the SDK).

1. Sidebar → **Project Overview** (top of the menu).
2. Click the **`</>`** (Web) icon to register a new web app.
3. App nickname: `demaestro-web`. Leave hosting unchecked. Click **Register app**.
4. Firebase shows you a config block like:

   ```js
   const firebaseConfig = {
     apiKey: "AIza...",
     authDomain: "demaestro.firebaseapp.com",
     projectId: "demaestro",
     storageBucket: "demaestro.appspot.com",
     messagingSenderId: "1234567890",
     appId: "1:1234567890:web:abcdef..."
   };
   ```

5. Copy each value — you'll paste them into `frontend/.env` shortly.
6. Click **Continue to console**.

### 2.6 Get the backend (Service Account) credentials

The FastAPI backend authenticates *as* Firebase using a service account JSON file. **This file is a secret — never commit it to Git.**

1. Sidebar → click the gear icon next to "Project Overview" → **Project settings**.
2. Top tabs → **Service accounts**.
3. Click **Generate new private key** → **Generate key**. A JSON file downloads.
4. Move it into `backend/secrets/firebase-service-account.json` (create the `secrets/` folder).
5. **Verify** that `backend/secrets/` is listed in `.gitignore` (it already is in this skeleton).

### 2.7 Get the Gemini API key

1. Go to <https://aistudio.google.com/apikey>.
2. Sign in with the same Google account.
3. Click **Create API key** → choose your Firebase project (or "Create a new project").
4. Copy the API key. Save it for `backend/.env` shortly.

### 2.8 Get the Anthropic API key (for Claude)

1. Go to <https://console.anthropic.com/>.
2. Sign in or create an account.
3. **Settings → API Keys → Create Key.** Name it `demaestro-dev`.
4. Copy the key (it starts with `sk-ant-...`). Save it for `backend/.env`.

> Anthropic gives free trial credit to new accounts; for Phase B you should not exceed the trial.

---

## 3. GitHub: fresh repository

You said you'd start with a fresh repo and push to your existing `owisezoubi/DeMaestro` later. Here is the clean path.

### 3.1 Create a new local repo

Once you've copied the skeleton to your machine (Section 4), initialize Git:

```bash
cd /path/to/demaestro
git init
git add .
git commit -m "Initial scaffold: FastAPI + React + Firebase auth shell"
```

### 3.2 Create the GitHub repo

1. Sign in at <https://github.com>.
2. Top-right → **+ → New repository**.
3. Repository name: `demaestro-phase-b` (or any fresh name).
4. **Private** (recommended until grading is done).
5. **Do not** initialize with README, .gitignore, or license — we already have those.
6. Click **Create repository**.

GitHub shows the push command:

```bash
git remote add origin https://github.com/<your-username>/demaestro-phase-b.git
git branch -M main
git push -u origin main
```

### 3.3 Later — push to your existing book repo

When you want to consolidate into your Phase A repo:

```bash
git remote add phase-a https://github.com/owisezoubi/DeMaestro.git
git push phase-a main
```

Or if you want a clean slate on the existing repo, use a force push (only after backing up the Phase A book PDF/sources):

```bash
git push phase-a main --force
```

---

## 4. First run

After scaffolding, the project structure will be:

```
demaestro/
├── frontend/         # React + Vite + Tailwind
├── backend/          # FastAPI + Firebase Admin
├── docker-compose.yml
├── README.md
├── SETUP_GUIDE.md    # this file
├── BOOK_CHANGES.md
└── AI_PROMPT.md
```

### 4.1 Configure environment variables

Copy each `.env.example` to `.env` and fill in real values:

```bash
cd frontend
cp .env.example .env
# Edit .env with the firebaseConfig values from Section 2.5

cd ../backend
cp .env.example .env
# Edit .env with the Gemini and Anthropic keys from Sections 2.7 and 2.8
mkdir -p secrets
# Place the service account JSON (Section 2.6) at secrets/firebase-service-account.json
```

### 4.2 Install dependencies

**Frontend:**
```bash
cd frontend
npm install
```

**Backend:**
```bash
cd ../backend
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate            # Windows PowerShell
pip install -r requirements.txt
```

### 4.3 Run the dev servers

Open **two terminals**.

**Terminal 1 — Frontend:**
```bash
cd frontend
npm run dev
```
Output should show: `Local: http://localhost:5173/`

**Terminal 2 — Backend:**
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```
Output should show: `Uvicorn running on http://127.0.0.1:8000`

### 4.4 Verify it works

1. Open <http://localhost:5173> in a browser → you should see the DeMaestro login screen.
2. Open <http://localhost:8000/docs> → you should see FastAPI's auto-generated Swagger UI.
3. Open <http://localhost:8000/health> → should return `{"status":"ok"}`.
4. On the frontend, sign up with a test email (any email, any password ≥ 6 chars). After signup you should see the empty Dashboard, and a new user should appear under **Authentication → Users** in the Firebase console.

If all four checks pass, the skeleton is healthy and you are ready for Week 2 (chat input + PDF upload + project CRUD).

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error: Cannot find module 'firebase'` | npm install didn't complete | `cd frontend && rm -rf node_modules package-lock.json && npm install` |
| `ModuleNotFoundError: No module named 'firebase_admin'` | virtualenv not active | Activate `.venv` then re-run pip install |
| Login fails with `auth/api-key-not-valid` | wrong/missing `VITE_FIREBASE_API_KEY` in `frontend/.env` | Recopy from Firebase console (Section 2.5), restart `npm run dev` |
| Backend `401 Unauthorized` | backend can't read service account JSON | Check path in `backend/.env` and that the file exists at `backend/secrets/firebase-service-account.json` |
| `docker info` fails | Docker Desktop not running | Start Docker Desktop and wait until the whale icon stops animating |
| CORS error in browser | Backend not allowing `localhost:5173` | Already configured in skeleton; verify `app/main.py` has the right `allow_origins` |

---

## 6. Daily workflow

After the first setup, your daily routine is:

```bash
# Terminal 1
cd demaestro/frontend && npm run dev

# Terminal 2
cd demaestro/backend && source .venv/bin/activate && uvicorn app.main:app --reload

# Make code changes — both servers hot-reload automatically.

# When done:
git add .
git commit -m "feat: <what you did>"
git push
```
