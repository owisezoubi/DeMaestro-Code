import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  CheckCircle2,
  AlertCircle,
  Download,
  Code2,
  FlaskConical,
  Shield,
  Package,
  Loader2,
} from 'lucide-react'

import Logo from '../components/Logo'
import { getGenerationStatus } from '../api/generation'

// Ordered pipeline stages — each maps to one or more project statuses
const STAGES = [
  {
    id: 'generate',
    label: 'Generate',
    description: 'Writing source code',
    icon: Code2,
    statuses: ['generating', 'generated'],
  },
  {
    id: 'test',
    label: 'Test',
    description: 'Running checks',
    icon: FlaskConical,
    statuses: ['testing', 'tested'],
  },
  {
    id: 'verify',
    label: 'Verify',
    description: 'Code quality scan',
    icon: Shield,
    statuses: ['verifying', 'verified'],
  },
  {
    id: 'package',
    label: 'Package',
    description: 'Creating ZIP',
    icon: Package,
    statuses: ['packaging', 'ready', 'ready_with_warnings'],
  },
]

// Map every project status to a stage index
const STATUS_TO_STAGE = {
  approved: -1,
  generating: 0,
  generated: 0,
  testing: 1,
  tested: 1,
  verifying: 2,
  verified: 2,
  packaging: 3,
  ready: 3,
  ready_with_warnings: 3,
}

const DONE_STATUSES = new Set(['ready', 'ready_with_warnings'])

function getStageState(stageIdx, currentStatus) {
  if (DONE_STATUSES.has(currentStatus)) return 'completed'
  if (currentStatus === 'failed') return 'failed'

  const currentStageIdx = STATUS_TO_STAGE[currentStatus] ?? -1

  if (stageIdx < currentStageIdx) return 'completed'
  if (stageIdx === currentStageIdx) return 'active'
  return 'pending'
}

export default function ProjectGeneration() {
  const { id: projectId } = useParams()

  const { data: status, isLoading, error } = useQuery({
    queryKey: ['generationStatus', projectId],
    queryFn: () => getGenerationStatus(projectId),
    // React Query v5: refetchInterval receives the query object
    refetchInterval: (query) => {
      const s = query.state.data?.status
      if (s === 'ready' || s === 'ready_with_warnings' || s === 'failed') return false
      return 2000
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="card w-96 border-red-200 bg-red-50">
          <h2 className="text-base font-semibold text-red-700 mb-2">Error Loading Status</h2>
          <p className="text-sm text-red-600 mb-4">{error.friendlyMessage || error.message}</p>
          <button className="btn-primary w-full" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>
    )
  }

  const currentStatus = status?.status ?? 'generating'
  const isDone = currentStatus === 'ready' || currentStatus === 'ready_with_warnings'
  const isFailed = currentStatus === 'failed'
  const isInProgress = !isDone && !isFailed

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <Link to="/dashboard" className="btn-secondary text-sm">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Dashboard
          </Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        {/* Page title */}
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-slate-900 mb-1">
            {isDone ? 'Your App is Ready!' : isFailed ? 'Generation Failed' : 'Building Your App'}
          </h1>
          <p className="text-sm text-slate-500">
            {isDone && 'Your application has been generated and packaged.'}
            {isFailed && 'Something went wrong. See the error below.'}
            {isInProgress && 'Hang tight — Claude is writing your complete application…'}
          </p>
        </div>

        {/* ── Pipeline timeline ── */}
        <div className="card">
          <div className="flex items-start justify-between gap-2">
            {STAGES.map((stage, idx) => {
              const state = getStageState(idx, currentStatus)
              const Icon = stage.icon
              const isLast = idx === STAGES.length - 1

              return (
                <div key={stage.id} className="flex flex-col items-center flex-1 relative">
                  {/* Connector line (between stages) */}
                  {!isLast && (
                    <div
                      className={`absolute top-6 left-1/2 w-full h-0.5 transition-colors duration-500 ${
                        state === 'completed' ? 'bg-emerald-400' : 'bg-slate-200'
                      }`}
                    />
                  )}

                  {/* Stage circle */}
                  <div
                    className={`relative z-10 w-12 h-12 rounded-full flex items-center justify-center mb-2 transition-all duration-300 ${
                      state === 'completed'
                        ? 'bg-emerald-100 text-emerald-600 ring-2 ring-emerald-300'
                        : state === 'active'
                        ? 'bg-primary-100 text-primary-600 ring-2 ring-primary-400 animate-pulse'
                        : state === 'failed'
                        ? 'bg-red-100 text-red-500'
                        : 'bg-slate-100 text-slate-400'
                    }`}
                  >
                    {state === 'completed' ? (
                      <CheckCircle2 size={24} />
                    ) : state === 'failed' ? (
                      <AlertCircle size={24} />
                    ) : (
                      <Icon size={24} />
                    )}
                  </div>

                  <p
                    className={`text-xs font-semibold text-center ${
                      state === 'active' ? 'text-primary-700' : 'text-slate-500'
                    }`}
                  >
                    {stage.label}
                  </p>
                  <p className="text-xs text-slate-400 text-center mt-0.5 hidden sm:block">
                    {stage.description}
                  </p>
                </div>
              )
            })}
          </div>
        </div>

        {/* ── In-progress activity card ── */}
        {isInProgress && (
          <div className="card space-y-4">
            <div className="flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin text-primary-600" />
              <h2 className="text-sm font-semibold text-slate-700">Current Activity</h2>
            </div>

            <div>
              <p className="text-xs text-slate-500 uppercase tracking-wide mb-1">Status</p>
              <p className="text-sm font-medium text-primary-700 capitalize">
                {currentStatus.replace(/_/g, ' ')}
              </p>
            </div>

            {/* File generation progress bar */}
            {status?.total_files > 0 && (
              <div>
                <div className="flex justify-between text-xs text-slate-500 mb-1">
                  <span>Files generated</span>
                  <span>
                    {status.generated_count ?? 0} / {status.total_files}
                  </span>
                </div>
                <div className="w-full bg-slate-200 rounded-full h-1.5">
                  <div
                    className="bg-primary-600 h-1.5 rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.round(
                        ((status.generated_count ?? 0) / status.total_files) * 100
                      )}%`,
                    }}
                  />
                </div>
              </div>
            )}

            {/* Test results checks */}
            {status?.test_results?.passed_checks && (
              <div>
                <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">Test Results</p>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(status.test_results.passed_checks).map(([check, passed]) => (
                    <div key={check} className="flex items-center gap-1.5">
                      {passed ? (
                        <CheckCircle2 size={14} className="text-emerald-500 flex-shrink-0" />
                      ) : (
                        <AlertCircle size={14} className="text-red-500 flex-shrink-0" />
                      )}
                      <span className="text-xs text-slate-600 capitalize">{check}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Success screen ── */}
        {isDone && (
          <SuccessScreen
            zipUrl={status.zip_url}
            hasWarnings={currentStatus === 'ready_with_warnings'}
            lastError={status.last_error}
          />
        )}

        {/* ── Error screen ── */}
        {isFailed && <ErrorScreen errorMessage={status.error_message} />}
      </main>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SuccessScreen({ zipUrl, hasWarnings, lastError }) {
  return (
    <div className="space-y-5">
      {/* Banner */}
      <div
        className={`rounded-xl text-white p-8 text-center shadow-md ${
          hasWarnings
            ? 'bg-gradient-to-r from-amber-500 to-orange-500'
            : 'bg-gradient-to-r from-emerald-500 to-teal-500'
        }`}
      >
        <div className="text-5xl mb-3">{hasWarnings ? '⚠️' : '🎉'}</div>
        <h2 className="text-2xl font-bold mb-1">
          {hasWarnings ? 'App Ready (with warnings)' : 'Your App is Ready!'}
        </h2>
        <p className="text-sm opacity-90">
          {hasWarnings
            ? 'The code was packaged but some tests did not pass — review before deploying.'
            : 'Your full-stack application has been generated and packaged.'}
        </p>
      </div>

      {/* Warning detail */}
      {hasWarnings && lastError && (
        <div className="card border-amber-200 bg-amber-50 space-y-1">
          <p className="text-xs font-semibold text-amber-800">Test warning</p>
          <p className="text-xs text-amber-700 font-mono break-words">{lastError}</p>
        </div>
      )}

      {/* Download */}
      <div className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-700">Download your code</h3>
        <p className="text-sm text-slate-500">
          Your project is packaged as a ZIP archive. The download link is valid for 7 days.
        </p>
        {zipUrl ? (
          <a
            href={zipUrl}
            download
            className="btn-primary flex items-center justify-center w-full"
          >
            <Download size={15} className="mr-2" />
            Download ZIP
          </a>
        ) : (
          <button className="btn-primary w-full" disabled>
            <Loader2 size={15} className="mr-2 animate-spin" />
            Preparing download…
          </button>
        )}
      </div>

      {/* Next steps */}
      <div className="card bg-primary-50 border-primary-100 space-y-2">
        <h3 className="text-sm font-semibold text-primary-800">Next Steps</h3>
        <ul className="space-y-1.5 text-sm text-primary-700">
          <li>📦 Download the ZIP and extract it locally</li>
          <li>🚀 Deploy to Vercel, Railway, or any cloud host of your choice</li>
          <li>🔄 Start a new project anytime from the Dashboard</li>
        </ul>
      </div>
    </div>
  )
}

function ErrorScreen({ errorMessage }) {
  return (
    <div className="card border-red-200 bg-red-50 space-y-4">
      <div className="flex items-center gap-2">
        <AlertCircle size={20} className="text-red-600 flex-shrink-0" />
        <h2 className="text-sm font-semibold text-red-700">Generation Failed</h2>
      </div>

      {errorMessage && (
        <div className="bg-red-100 border border-red-200 rounded-lg p-3">
          <p className="text-xs font-mono text-red-700 break-words">{errorMessage}</p>
        </div>
      )}

      <p className="text-sm text-slate-600">
        Something went wrong during code generation. You can try again or contact support.
      </p>

      <button
        className="btn-primary bg-red-600 hover:bg-red-700"
        onClick={() => window.location.reload()}
      >
        Try Again
      </button>
    </div>
  )
}
