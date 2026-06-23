import { useState, useEffect } from 'react'
import { Rocket, ExternalLink, Loader2 } from 'lucide-react'
import { api } from '../api/client'

const HARD_TIMEOUT_MS = 10 * 60 * 1000

export default function DeploymentPanel({ projectId, uid }) {
  const [status, setStatus] = useState(null)
  const [url, setUrl] = useState(null)
  const [error, setError] = useState(null)

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
        if (d.status !== 'ready' && d.status !== 'failed') {
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
  }, [projectId])

  const handleRedeploy = async () => {
    setStatus('deploying')
    setError(null)
    try {
      await api.post(`/projects/${projectId}/deploy`)
    } catch {
      // polling will surface the real outcome
    }
  }

  if (status === 'deploying') {
    return (
      <div className="p-5 rounded-xl border border-surface-border bg-surface-panel space-y-2">
        <div className="flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin text-accent" />
          <p className="font-medium text-text-default text-sm">Deploying to Vercel…</p>
        </div>
        <p className="text-xs text-text-muted">Usually 60–90 seconds. URL appears when ready.</p>
      </div>
    )
  }

  if (status === 'ready' && url) {
    return (
      <div className="space-y-4">
        {/* Animated Open Live App button */}
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="group relative inline-flex items-center gap-3
                     w-full justify-center
                     px-8 py-4 rounded-xl overflow-hidden
                     bg-gradient-to-r from-accent via-accent-secondary to-accent
                     bg-[length:200%_auto] animate-gradient
                     text-white font-bold text-base
                     shadow-xl shadow-accent/30
                     hover:shadow-2xl hover:shadow-accent/40
                     hover:scale-[1.02] active:scale-[0.98]
                     transition-all duration-300"
        >
          <span className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.25),transparent_60%)]
                           opacity-0 group-hover:opacity-100 transition-opacity" />
          <Rocket className="w-5 h-5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform relative z-10" />
          <span className="relative z-10">Open Live App</span>
          <ExternalLink className="w-4 h-4 opacity-70 relative z-10" />
        </a>
        <div className="flex items-center justify-between">
          <p className="text-xs text-text-muted break-all">{url}</p>
          <button
            onClick={handleRedeploy}
            className="ml-3 text-xs text-accent hover:underline shrink-0"
          >
            Redeploy
          </button>
        </div>
      </div>
    )
  }

  if (status === 'failed') {
    return (
      <div className="p-4 rounded-xl border border-error/30 bg-error/5 space-y-3">
        <p className="font-medium text-error text-sm">Deployment failed</p>
        {error && <p className="text-xs text-error/80 break-all">{error}</p>}
        <button
          onClick={handleRedeploy}
          className="px-4 py-2 rounded-lg bg-error text-white text-xs font-medium
                     hover:opacity-90 transition-opacity"
        >
          Try again
        </button>
      </div>
    )
  }

  // Null state: offer a manual deploy button
  return (
    <button
      onClick={handleRedeploy}
      className="w-full px-4 py-3 rounded-xl border border-surface-border
                 bg-surface-panel text-text-default text-sm font-medium
                 hover:border-accent/50 hover:bg-accent/5
                 transition-all duration-200"
    >
      Deploy to Vercel
    </button>
  )
}
