import { useState, useMemo } from 'react'
import {
  SandpackProvider,
  SandpackPreview,
  SandpackLayout,
} from '@codesandbox/sandpack-react'
import { Monitor, RefreshCw, Maximize2, AlertTriangle, Info } from 'lucide-react'

// ─── Constants ────────────────────────────────────────────────────────────────

// Extensions that Sandpack's bundler can actually process
const SANDPACK_SUPPORTED_EXTS = new Set([
  'js', 'jsx', 'ts', 'tsx', 'css', 'scss', 'html', 'json', 'svg',
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'woff', 'woff2',
  'ttf', 'eot', 'md',
])

// Backend-only files that must never reach Sandpack
const BACKEND_PREFIXES = ['backend/', 'docker', 'nginx', '.github', 'scripts/']
const BACKEND_FILES = new Set([
  'docker-compose.yml', 'docker-compose.yaml', 'Dockerfile',
  '.dockerignore', '.gitignore', 'README.md', 'Makefile',
])

// ─── File extraction helpers ──────────────────────────────────────────────────

/**
 * Extract only frontend-runnable files from the full generated_files dict.
 *
 * The backend stores files as:
 *   "frontend/src/App.jsx"  → code
 *   "frontend/package.json" → json
 *   "backend/app/main.py"   → python
 *
 * We strip the "frontend/" prefix and discard everything else.
 */
function extractFrontendFiles(allFiles) {
  const out = {}

  for (const [rawPath, code] of Object.entries(allFiles)) {
    // Skip backend files by prefix
    if (BACKEND_PREFIXES.some(p => rawPath.startsWith(p))) continue
    // Skip known root-level backend files
    if (BACKEND_FILES.has(rawPath)) continue

    // Strip "frontend/" prefix
    const strippedPath = rawPath.startsWith('frontend/')
      ? rawPath.slice('frontend/'.length)
      : rawPath

    // Only include files with supported extensions
    const ext = strippedPath.split('.').pop()?.toLowerCase() || ''
    if (!SANDPACK_SUPPORTED_EXTS.has(ext)) continue

    // Normalise to leading slash for Sandpack
    const key = strippedPath.startsWith('/') ? strippedPath : `/${strippedPath}`
    out[key] = typeof code === 'string' ? code : String(code ?? '')
  }

  return out
}

/**
 * Parse frontend/package.json to pull out real npm dependencies.
 * Returns { dependencies, devDependencies } objects for Sandpack's customSetup.
 */
function parseDependencies(allFiles) {
  const raw = allFiles['frontend/package.json'] || allFiles['package.json'] || ''
  if (!raw) return { dependencies: {}, devDependencies: {} }

  try {
    const pkg = JSON.parse(raw)
    const dependencies = pkg.dependencies || {}
    const devDependencies = pkg.devDependencies || {}

    // Remove build tools Sandpack doesn't need (and can't use)
    const buildToolBlacklist = ['vite', '@vitejs/plugin-react', 'esbuild', 'rollup', 'webpack']
    for (const tool of buildToolBlacklist) {
      delete devDependencies[tool]
    }

    return { dependencies, devDependencies }
  } catch {
    return { dependencies: {}, devDependencies: {} }
  }
}

/**
 * Detect the best Sandpack template from the file set.
 *   - Has .jsx/.tsx files or react in package.json → "react"
 *   - Has index.html only                          → "static"
 */
function detectTemplate(frontendFiles, allFiles) {
  const paths = Object.keys(frontendFiles)
  const hasJsx = paths.some(p => /\.(jsx|tsx)$/.test(p))
  if (hasJsx) return 'react'

  try {
    const pkgRaw = allFiles['frontend/package.json'] || allFiles['package.json'] || ''
    if (pkgRaw.includes('"react"')) return 'react'
  } catch { /* ignore */ }

  return 'static'
}

/**
 * Find the main entry file from the extracted frontend files.
 * Priority: /index.jsx → /src/index.jsx → /src/main.jsx → /App.jsx → /index.html
 */
function findEntryFile(frontendFiles) {
  const candidates = [
    '/index.jsx', '/index.tsx', '/index.js',
    '/src/index.jsx', '/src/index.tsx', '/src/index.js',
    '/src/main.jsx', '/src/main.tsx', '/src/main.js',
    '/App.jsx', '/App.tsx',
    '/index.html',
  ]
  return candidates.find(c => frontendFiles[c]) ?? Object.keys(frontendFiles)[0]
}

/**
 * Sandpack needs an /index.js entry that imports App if only App.jsx exists
 * and there's no index.* file. This auto-generates a minimal one.
 */
function ensureEntryPoint(frontendFiles) {
  const files = { ...frontendFiles }

  const hasIndex = Object.keys(files).some(p =>
    /^\/(src\/)?index\.(js|jsx|ts|tsx)$/.test(p)
  )
  if (hasIndex) return files

  // Check if there's an App.jsx to wrap
  const appFile =
    files['/App.jsx'] ? '/App.jsx' :
    files['/App.tsx'] ? '/App.tsx' :
    files['/src/App.jsx'] ? '/src/App.jsx' :
    files['/src/App.tsx'] ? '/src/App.tsx' :
    null

  if (!appFile) return files

  // Generate a minimal index.jsx that bootstraps the app
  const srcPath = appFile.startsWith('/src/') ? appFile : null
  const importPath = srcPath ? './App' : './App'

  files[srcPath ? '/src/index.jsx' : '/index.jsx'] = `import React from 'react';
import ReactDOM from 'react-dom/client';
import App from '${importPath}';
${files['/src/index.css'] ? "import './index.css';" : files['/index.css'] ? "import './index.css';" : ''}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
`
  return files
}

// ─── Path-alias resolver ─────────────────────────────────────────────────────

/**
 * Compute a relative path from a source directory to a target absolute path.
 * Both arguments should be absolute Sandpack paths (leading slash, no trailing slash).
 *
 * Examples:
 *   relativize('/src', '/src/contexts/Auth')  → './contexts/Auth'
 *   relativize('/src/components', '/src/utils/helper') → '../utils/helper'
 */
function relativize(fromDir, toPath) {
  const from = fromDir.split('/').filter(Boolean)  // e.g. ['src', 'components']
  const to   = toPath.split('/').filter(Boolean)   // e.g. ['src', 'utils', 'helper']

  // Find common prefix length
  let i = 0
  while (i < from.length && i < to.length && from[i] === to[i]) i++

  const ups   = from.slice(i).map(() => '..')      // go up from source
  const downs = to.slice(i)                        // go down to target
  const parts = [...ups, ...downs]

  if (parts.length === 0) return '.'
  const result = parts.join('/')
  return result.startsWith('.') ? result : `./${result}`
}

/**
 * Rewrite Vite-style path aliases inside every JS/JSX/TS/TSX file so Sandpack
 * can resolve them. The standard Vite alias is `@` → `./src`.
 *
 * Handles:
 *   import X from '@/foo/bar'        → import X from './foo/bar'  (from /src/)
 *   import { X } from "@/utils"      → relative equivalent
 *   import '@/styles/global.css'     → relative equivalent
 *   dynamic import('@/components/X') → relative equivalent
 */
function resolvePathAliases(files) {
  // Standard Vite alias: @ maps to /src
  const ALIAS_TARGET = '/src'
  const ALIAS_PREFIX = '@/'

  const out = {}
  for (const [filePath, code] of Object.entries(files)) {
    if (!code || typeof code !== 'string' || !code.includes(ALIAS_PREFIX)) {
      out[filePath] = code
      continue
    }

    // Directory of this file (e.g. '/src/components' from '/src/components/Navbar.jsx')
    const fileDir = filePath.slice(0, filePath.lastIndexOf('/')) || '/'

    // Replace every quoted '@/...' occurrence with a relative path
    out[filePath] = code.replace(
      /(['"])@\/([^'"\n]+)\1/g,
      (_, quote, importPath) => {
        const targetAbs = `${ALIAS_TARGET}/${importPath}`
        const rel = relativize(fileDir, targetAbs)
        return `${quote}${rel}${quote}`
      }
    )
  }
  return out
}

// ─── import.meta.env replacer ───────────────────────────────────────────────

/**
 * Sandpack's bundler evaluates files as CommonJS scripts (not native ESM
 * modules), so `import.meta` is a SyntaxError at runtime. Generated Vite apps
 * use import.meta.env.VITE_* everywhere. We replace every occurrence with a
 * safe literal BEFORE Sandpack ever sees the code.
 *
 * Replacements are applied most-specific → least-specific so broader patterns
 * don't swallow tokens that a narrower pattern should have handled.
 * Handles optional chaining (`?.`), bracket notation, and any casing.
 */
function replaceImportMeta(files) {
  const JS_EXTS = new Set(['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs'])
  const ENV_STUB = '({ MODE: "development", DEV: true, PROD: false, BASE_URL: "/" })'

  const out = {}
  for (const [filePath, code] of Object.entries(files)) {
    const ext = filePath.split('.').pop()?.toLowerCase() || ''
    if (!code || typeof code !== 'string' || !JS_EXTS.has(ext) || !code.includes('import.meta')) {
      out[filePath] = code
      continue
    }

    let r = code

    // ── 1. VITE_ variables (most common case) ──────────────────────────────
    // Matches with or without optional chaining, any word chars after VITE_
    r = r.replace(/import\.meta\??\.env\??\.VITE_\w*/g, '""')

    // ── 2. Vite built-in env flags ─────────────────────────────────────────
    r = r
      .replace(/import\.meta\??\.env\??\.MODE/g,     '"development"')
      .replace(/import\.meta\??\.env\??\.DEV/g,      'true')
      .replace(/import\.meta\??\.env\??\.PROD/g,     'false')
      .replace(/import\.meta\??\.env\??\.SSR/g,      'false')
      .replace(/import\.meta\??\.env\??\.BASE_URL/g, '"/"')

    // ── 3. Bracket notation: import.meta.env['VITE_KEY'] ──────────────────
    r = r.replace(/import\.meta\??\.env\?\s*\[(['"])[^\1]+\1\]/g, '""')

    // ── 4. Bare import.meta.env (spread, dynamic access, assignment) ───────
    r = r.replace(/import\.meta\??\.env/g, ENV_STUB)

    // ── 5. import.meta.hot (Vite HMR — unavailable in Sandpack) ───────────
    r = r.replace(/import\.meta\??\.hot/g, 'undefined')

    // ── 6. import.meta.url (module URL — unavailable in Sandpack) ─────────
    r = r.replace(/import\.meta\??\.url/g, '""')

    // ── 7. Any other import.meta.<identifier> we haven't covered ───────────
    r = r.replace(/import\.meta\??\.([a-zA-Z_$][\w$]*)/g, 'undefined')

    // ── 8. Final fallback: bare `import.meta` (e.g. passed as argument) ────
    r = r.replace(/import\.meta/g, '({})')

    out[filePath] = r
  }
  return out
}

// ─── Refresh key counter (module-level so it survives re-renders) ─────────────
let _key = 0

// ─── Component ───────────────────────────────────────────────────────────────

export default function LivePreviewCanvas({ files }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const [hasError, setHasError] = useState(false)

  // Extract and process frontend files
  // Pipeline: extract → alias-resolve → import.meta polyfill → entry-point → Sandpack
  const frontendFilesRaw    = useMemo(() => extractFrontendFiles(files || {}), [files])
  const frontendFilesAlias  = useMemo(() => resolvePathAliases(frontendFilesRaw), [frontendFilesRaw])
  const frontendFilesMeta   = useMemo(() => replaceImportMeta(frontendFilesAlias), [frontendFilesAlias])
  const frontendFiles       = useMemo(() => ensureEntryPoint(frontendFilesMeta), [frontendFilesMeta])
  const { dependencies, devDependencies } = useMemo(() => parseDependencies(files || {}), [files])
  const template   = useMemo(() => detectTemplate(frontendFiles, files || {}), [frontendFiles, files])
  const activeFile = useMemo(() => findEntryFile(frontendFiles), [frontendFiles])

  const totalFrontendFiles = Object.keys(frontendFiles).length
  const totalFiles = Object.keys(files || {}).length
  const backendOnlyFiles = totalFiles - totalFrontendFiles

  function handleRefresh() {
    _key++
    setRefreshKey(_key)
    setHasError(false)
  }

  // Nothing to preview
  if (!files || totalFiles === 0) {
    return (
      <div className="flex items-center justify-center h-64 rounded-xl border border-surface-border bg-surface-panel text-text-muted text-sm">
        No files to preview yet.
      </div>
    )
  }

  // No renderable frontend files found
  if (totalFrontendFiles === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 rounded-xl border border-surface-border bg-surface-panel gap-3 text-center p-6">
        <Info className="w-8 h-8 text-text-muted" />
        <div>
          <p className="text-sm font-medium text-text-default">No frontend files detected</p>
          <p className="text-xs text-text-muted mt-1">
            This project appears to be backend-only. Use the Project Explorer to browse its files.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div
      className={`flex flex-col rounded-xl border border-surface-border overflow-hidden bg-surface-panel ${
        fullscreen ? 'fixed inset-4 z-50 shadow-2xl' : ''
      }`}
    >
      {/* ── Top chrome bar ── */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-surface-page/70 backdrop-blur-sm flex-shrink-0">
        {/* Left: traffic lights + label */}
        <div className="flex items-center gap-2.5">
          <span className="w-3 h-3 rounded-full bg-red-400/80 flex-shrink-0" />
          <span className="w-3 h-3 rounded-full bg-yellow-400/80 flex-shrink-0" />
          <span className="w-3 h-3 rounded-full bg-green-400/80 flex-shrink-0" />
          <div className="ml-2 flex items-center gap-2 text-xs text-text-muted">
            <Monitor className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="font-medium">Live Preview</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-accent/10 text-accent font-semibold uppercase tracking-wider">
              {template}
            </span>
            <span className="text-text-muted/60">
              {totalFrontendFiles} frontend file{totalFrontendFiles !== 1 ? 's' : ''}
              {backendOnlyFiles > 0 ? ` · ${backendOnlyFiles} backend file${backendOnlyFiles !== 1 ? 's' : ''} hidden` : ''}
            </span>
          </div>
        </div>

        {/* Right: controls */}
        <div className="flex items-center gap-1">
          {hasError && (
            <div className="flex items-center gap-1 text-xs text-amber-500 mr-2">
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
              <span>Runtime error</span>
            </div>
          )}
          <button
            onClick={handleRefresh}
            title="Reload preview"
            className="p-1.5 rounded-md text-text-muted hover:text-text-default hover:bg-surface-border transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setFullscreen(f => !f)}
            title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
            className="p-1.5 rounded-md text-text-muted hover:text-text-default hover:bg-surface-border transition-colors"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* ── Sandpack preview ── */}
      <div
        className="flex-1 min-h-0"
        style={{ height: fullscreen ? 'calc(100% - 80px)' : '560px' }}
      >
        <SandpackProvider
          key={refreshKey}
          template={template}
          files={frontendFiles}
          customSetup={{
            dependencies,
            devDependencies,
            entry: activeFile,
          }}
          options={{
            activeFile,
            visibleFiles: Object.keys(frontendFiles),
            recompileMode: 'delayed',
            recompileDelay: 600,
            externalResources: [
              // Google Fonts (in case the generated CSS references them)
              'https://fonts.googleapis.com',
              'https://fonts.gstatic.com',
            ],
          }}
          theme="auto"
        >
          <SandpackLayout style={{ height: '100%', border: 'none', borderRadius: 0 }}>
            <SandpackPreview
              style={{ height: '100%', flex: 1 }}
              showOpenInCodeSandbox={false}
              showRefreshButton={false}
              onError={() => setHasError(true)}
            />
          </SandpackLayout>
        </SandpackProvider>
      </div>

      {/* ── Footer ── */}
      <div className="flex items-center justify-between px-4 py-1.5 border-t border-surface-border bg-surface-page/40 flex-shrink-0">
        <p className="text-[10px] text-text-muted">
          Powered by CodeSandbox Bundler — frontend files only (backend not executed)
        </p>
        <p className="text-[10px] text-text-muted">
          API calls will fail gracefully — download the ZIP to run the full stack
        </p>
      </div>
    </div>
  )
}
