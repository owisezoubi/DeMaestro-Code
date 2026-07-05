import { useState, useEffect } from 'react'
import { Rocket, ExternalLink, AlertCircle, Copy, Check } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../api/client'

const HARD_TIMEOUT_MS = 10 * 60 * 1000

function StatusPill({ status }) {
  const styles = {
    ready:          'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
    ready_degraded: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
    deploying:      'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
    failed:         'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  }
  const labels = {
    ready:          'Live',
    ready_degraded: 'Live (degraded)',
    deploying:      'Deploying',
    failed:         'Failed',
  }
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${styles[status] || 'bg-surface-border text-text-muted'}`}>
      {labels[status] || 'Idle'}
    </span>
  )
}

export default function DeploymentPanel({ projectId, uid }) {
  const [status, setStatus] = useState(null)
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)
  const [displayName, setDisplayName] = useState(null)
  const [copied, setCopied] = useState(false)
  // Bumped by handleRedeploy to force the polling effect to restart.
  // Without this, polling stops when status hits 'failed' or 'ready' and
  // never resumes after a Redeploy click — leaving the UI frozen on
  // stale state even though the backend deploy completed.
  const [pollNonce, setPollNonce] = useState(0)

  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    let timer = null
    const start = Date.now()

    const tick = async () => {
      if (cancelled) return
      if (Date.now() - start > HARD_TIMEOUT_MS) return
      try {
        const r = await api.get(`/projects/${projectId}/deployment-status`)
        if (cancelled) return
        const d = r.data
        setStatus(d.status)
        setUrl(d.url)
        setError(d.error)
        setDisplayName(d.display_name || null)
        if (d.status !== 'ready' && d.status !== 'ready_degraded' && d.status !== 'failed') {
          timer = setTimeout(tick, 4000)
        }
      } catch {
        if (!cancelled) timer = setTimeout(tick, 8000)
      }
    }

    tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [projectId, pollNonce])

  const handleRedeploy = async () => {
    // Reset local state so the UI immediately reflects the new attempt,
    // then bump the nonce to restart the polling effect from scratch.
    setStatus('deploying')
    setUrl(null)
    setError(null)
    setDisplayName(null)
    setPollNonce((n) => n + 1)
    try {
      await api.post(`/projects/${projectId}/deploy`)
      // Bump again after the POST so the polling effect picks up the
      // backend's first state transition without waiting the 4s tick.
      setPollNonce((n) => n + 1)
    } catch {
      // polling will surface the real outcome
    }
  }

  async function handleCopyUrl() {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      toast.success('URL copied')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('Copy failed')
    }
  }

  const headerStatus = ['ready', 'ready_degraded', 'deploying', 'failed'].includes(status) ? status : null

  return (
    <div className="rounded-xl border border-surface-border bg-surface-panel overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-border bg-surface-page/40">
        <Rocket className="w-4 h-4 text-accent flex-shrink-0" />
        <span className="text-sm font-semibold text-text-default flex-1">Deployment</span>
        <StatusPill status={headerStatus} />
      </div>

      {/* Body */}
      <div className="p-4">
        {/* Deploying state */}
        {status === 'deploying' && (
          <div className="space-y-3">
            {/* Shimmer progress bar */}
            <div className="relative h-2 rounded-full overflow-hidden bg-surface-border">
              <div
                className="absolute inset-0 rounded-full"
                style={{
                  backgroundImage: 'linear-gradient(90deg, transparent 0%, rgb(var(--color-accent)/0.5) 40%, rgb(var(--color-accent-secondary)/0.7) 60%, transparent 100%)',
                  backgroundSize: '200% 100%',
                  animation: 'shimmer 2s linear infinite',
                }}
              />
            </div>
            <div className="flex items-center gap-2">
              <Rocket className="w-3.5 h-3.5 text-accent animate-bounce flex-shrink-0" />
              <p className="text-xs text-text-muted">Deploying… This usually takes 60–90 seconds.</p>
            </div>
          </div>
        )}

        {/* Ready with URL */}
        {status === 'ready' && url && (
          <div className="space-y-3">
            {displayName && (
              <div className="flex items-center gap-2 text-sm text-text-default">
                <Rocket className="w-4 h-4 text-accent flex-shrink-0" />
                <span>
                  <span className="text-text-muted">Deployed as</span>{' '}
                  <span className="font-semibold">{displayName}</span>
                </span>
              </div>
            )}
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="group relative inline-flex items-center gap-3
                         w-full justify-center
                         px-6 py-3 rounded-xl overflow-hidden
                         bg-gradient-to-r from-accent via-accent-secondary to-accent
                         bg-[length:200%_auto] animate-gradient
                         text-white font-bold text-sm
                         shadow-lg shadow-accent/25
                         hover:shadow-xl hover:shadow-accent/35
                         hover:scale-[1.01] active:scale-[0.99]
                         transition-all duration-300"
            >
              <span className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.2),transparent_60%)]
                               opacity-0 group-hover:opacity-100 transition-opacity" />
              <Rocket className="w-4 h-4 group-hover:-translate-y-0.5 transition-transform relative z-10" />
              <span className="relative z-10">Open Live App</span>
              <ExternalLink className="w-3.5 h-3.5 opacity-70 relative z-10" />
            </a>

            <div className="flex items-center gap-2">
              <code className="flex-1 text-[11px] font-mono text-text-muted
                               bg-surface-page border border-surface-border
                               rounded-lg px-3 py-1.5 truncate">
                {url}
              </code>
              <button
                onClick={handleCopyUrl}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs
                           bg-surface-panel border border-surface-border
                           text-text-muted hover:text-text-default hover:border-accent/40
                           transition-colors shrink-0"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>

            <button
              onClick={handleRedeploy}
              className="text-xs text-accent hover:underline"
            >
              Redeploy
            </button>
          </div>
        )}

        {/* Ready but no URL */}
        {status === 'ready' && !url && (
          <div className="space-y-3">
            <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800">
              <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">No live URL returned</p>
                <p className="text-xs text-amber-700 dark:text-amber-400 mt-0.5">
                  Deployment finished but no live URL was returned. Try redeploying.
                </p>
              </div>
            </div>
            <button
              onClick={handleRedeploy}
              className="w-full px-4 py-2 rounded-lg text-xs font-medium
                         bg-amber-600 hover:bg-amber-700 text-white transition-colors"
            >
              Redeploy
            </button>
          </div>
        )}

        {/* Degraded state — app is live but post-deploy smoke caught 500s */}
        {status === 'ready_degraded' && (
          <div className="space-y-3">
            <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800">
              <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">Deployed with warnings</p>
                <p className="text-xs text-amber-700 dark:text-amber-400 mt-0.5">
                  The app shipped but the post-deploy smoke test caught endpoints returning 500.
                </p>
                {error && (
                  <pre className="mt-2 text-[10px] font-mono text-amber-700 dark:text-amber-300 bg-amber-100/60 dark:bg-amber-950/40 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
                    {error}
                  </pre>
                )}
              </div>
            </div>
            {url && (
              <a href={url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">
                Open Live App ({url})
              </a>
            )}
            <button
              onClick={handleRedeploy}
              className="w-full px-4 py-2 rounded-lg text-xs font-medium
                         bg-amber-600 hover:bg-amber-700 text-white transition-colors"
            >
              Redeploy
            </button>
          </div>
        )}

        {/* Failed state */}
        {status === 'failed' && (
          <div className="space-y-3">
            <div className="flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-error flex-shrink-0 mt-0.5" />
              <p className="text-sm font-semibold text-error">Deployment failed</p>
            </div>
            {error && (
              <pre className="text-[11px] font-mono text-error/80 bg-error/5 border border-error/20 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all">
                {error}
              </pre>
            )}
            <button
              onClick={handleRedeploy}
              className="w-full px-4 py-2 rounded-lg text-xs font-medium
                         bg-gradient-to-r from-red-600 to-red-500 hover:from-red-700 hover:to-red-600
                         text-white transition-all duration-200"
            >
              Try again
            </button>
            <p className="text-[10px] text-text-muted">
              If the issue persists, check the project files — a regeneration may be required.
            </p>
          </div>
        )}

        {/* Idle / null state */}
        {!status && (
          <div className="space-y-2">
            <button
              onClick={handleRedeploy}
              className="w-full px-4 py-2.5 rounded-lg border border-surface-border
                         bg-surface-panel text-text-default text-sm font-medium
                         hover:border-accent/50 hover:bg-accent/5
                         transition-all duration-200"
            >
              Deploy to Vercel
            </button>
            <p className="text-[10px] text-text-muted text-center">
              Push this project to Vercel + Neon Postgres.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
