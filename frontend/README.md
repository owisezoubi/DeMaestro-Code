# DeMaestro Frontend

React + Vite + Tailwind CSS + Firebase Auth.

## Run locally

```bash
npm install
cp .env.example .env       # then fill in Firebase config from console
npm run dev
```

Open http://localhost:5173.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Start the Vite dev server with HMR |
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint check |
| `npm run format` | Prettier write |

## Folder layout

```
src/
├── auth/        Firebase client SDK init (auth, storage)
├── context/     React Context providers (AuthContext)
├── api/         axios client + typed wrappers per resource
├── pages/       Route-level components (Login, Signup, Dashboard, ...)
├── components/  Reusable UI pieces
├── App.jsx      Route definitions
├── main.jsx     Application entry; wraps providers
└── index.css    Tailwind layers + design-token utility classes
```
