import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useAuth } from '../context/AuthContext'
import {
  CheckCircle2,
  AlertCircle,
  AlertTriangle,
  Download,
  Code2,
  FlaskConical,
  Shield,
  Package,
  Rocket,
  Loader2,
  ChevronDown,
  ChevronRight,
  XCircle,
  Lock,
} from 'lucide-react'

import { getGenerationStatus, startGeneration, rerunTests, stopGeneration, restartFromScratch } from '../api/generation'
import DeploymentPanel from '../components/DeploymentPanel'
import GeneratedFileTree from '../components/GeneratedFileTree'

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
    statuses: ['packaging'],
  },
  {
    id: 'deploy',
    label: 'Deploy',
    description: 'Going live',
    icon: Rocket,
    statuses: ['deploying', 'ready', 'ready_with_warnings', 'deployed'],
  },
]

// Map every project status to a stage index
const STATUS_TO_STAGE = {
  approved: -1,
  stopped: -1,
  generating: 0,
  generated: 0,
  testing: 1,
  tested: 1,
  debugging: 1,
  tests_failed_recoverable: 1,
  verifying: 2,
  verified: 2,
  packaging: 3,
  ready: 4,
  ready_with_warnings: 4,
  deploying: 4,
  deployed: 4,
}

const DONE_STATUSES = new Set(['ready', 'ready_with_warnings', 'deployed'])
const TERMINAL_STATUSES = new Set(['ready', 'ready_with_warnings', 'deployed', 'failed', 'tests_failed_recoverable', 'stopped'])

function getStageState(stageIdx, currentStatus) {
  // All stages complete when app is deployed
  if (currentStatus === 'deployed') return 'completed'
  // Stages 0-3 complete when packaging is done / deploy phase starts
  if ((currentStatus === 'ready' || currentStatus === 'ready_with_warnings') && stageIdx < 4) return 'completed'
  if ((currentStatus === 'ready' || currentStatus === 'ready_with_warnings') && stageIdx === 4) return 'active'
  if (currentStatus === 'failed') return 'failed'
  if (currentStatus === 'tests_failed_recoverable') {
    // Generate stage is done; Test stage shows as failed
    if (stageIdx === 0) return 'completed'
    if (stageIdx === 1) return 'failed'
    return 'pending'
  }

  const currentStageIdx = STATUS_TO_STAGE[currentStatus] ?? -1

  if (stageIdx < currentStageIdx) return 'completed'
  if (stageIdx === currentStageIdx) return 'active'
  return 'pending'
}

export default function ProjectGeneration() {
  const { id: projectId } = useParams()
  const { user } = useAuth()

  const { data: status, isLoading, error } = useQuery({
    queryKey: ['generationStatus', projectId],
    queryFn: () => getGenerationStatus(projectId),
    // React Query v5: refetchInterval receives the query object
    refetchInterval: (query) => {
      const s = query.state.data?.status
      if (TERMINAL_STATUSES.has(s)) return false
      return 2000
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center">
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
  const isDone = currentStatus === 'ready' || currentStatus === 'ready_with_warnings' || currentStatus === 'deployed'
  const isFailed = currentStatus === 'failed'
  const isRecoverable = currentStatus === 'tests_failed_recoverable'
  const isStopped = currentStatus === 'stopped'
  const isInProgress = !isDone && !isFailed && !isRecoverable && !isStopped

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900">

      <main className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        {/* Blueprint / requirements panel */}
        {(status?.blueprint || status?.structured_requirements || status?.summary) && (
          <BlueprintPanel
            blueprint={status.blueprint}
            summary={status.summary}
            structuredRequirements={status.structured_requirements}
            projectId={projectId}
          />
        )}

        {/* Checklist panel */}
        {status?.checklist?.length > 0 && (
          <ChecklistPanel
            checklist={status.checklist}
            checklistResults={status.checklist_results || []}
            projectId={projectId}
          />
        )}

        {/* Page title */}
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100 mb-1">
            {isDone
              ? 'Your App is Ready!'
              : isFailed
              ? 'Generation Failed'
              : isRecoverable
              ? 'Tests Did Not Pass'
              : isStopped
              ? 'Generation Stopped'
              : 'Building Your App'}
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {isDone && 'Your application has been generated and packaged.'}
            {isFailed && 'Something went wrong. See the error below.'}
            {isRecoverable && 'Your files are intact — re-run tests or regenerate from scratch.'}
            {isStopped && 'Generation was stopped. You can restart from scratch.'}
            {isInProgress && 'Hang tight — Claude is writing your complete application...'}
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
                        state === 'completed' ? 'bg-emerald-400' : 'bg-slate-200 dark:bg-slate-700'
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
                        : 'bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500'
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
                      state === 'active' ? 'text-primary-700 dark:text-primary-400' : 'text-slate-500 dark:text-slate-400'
                    }`}
                  >
                    {stage.label}
                  </p>
                  <p className="text-xs text-slate-400 dark:text-slate-500 text-center mt-0.5 hidden sm:block">
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
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-primary-600" />
                <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Current Activity</h2>
              </div>
              <StopButton projectId={projectId} status={currentStatus} />
            </div>

            <div>
              <p className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">Status</p>
              <p className="text-sm font-medium text-primary-700 dark:text-primary-400 capitalize">
                {currentStatus.replace(/_/g, ' ')}
              </p>
            </div>

            {/* File generation progress bar */}
            {status?.total_files > 0 && (
              <div>
                <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400 mb-1">
                  <span>Files generated</span>
                  <span>
                    {status.generated_count ?? 0} / {status.total_files}
                  </span>
                </div>
                <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-1.5">
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
                <p className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-2">Test Results</p>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(status.test_results.passed_checks).map(([check, passed]) => (
                    <div key={check} className="flex items-center gap-1.5">
                      {passed ? (
                        <CheckCircle2 size={14} className="text-emerald-500 flex-shrink-0" />
                      ) : (
                        <AlertCircle size={14} className="text-red-500 flex-shrink-0" />
                      )}
                      <span className="text-xs text-slate-600 dark:text-slate-400 capitalize">{check}</span>
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
            testErrorLog={status.test_error_log}
            projectId={projectId}
            uid={user?.uid}
            generatedFiles={status.generated_files || {}}
          />
        )}

        {/* ── Error screen ── */}
        {isFailed && <ErrorScreen errorMessage={status.error_message} projectId={projectId} />}

        {isRecoverable && (
          <RecoverableFailureScreen
            projectId={projectId}
            lastError={status.last_error}
            realFailures={status.real_failures || []}
            testErrorLog={status.test_error_log}
          />
        )}

        {/* ── Stopped screen ── */}
        {isStopped && (
          <div className="card border-slate-200 bg-slate-50 dark:bg-slate-800/50 space-y-4">
            <div className="flex items-center gap-2">
              <AlertCircle size={20} className="text-slate-500 flex-shrink-0" />
              <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Generation Stopped</h2>
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-400">
              The generation pipeline was stopped by request. You can restart it from scratch to generate a new version.
            </p>
            <RestartFromScratchButton projectId={projectId} status={currentStatus} />
          </div>
        )}
      </main>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SuccessScreen({ zipUrl, hasWarnings, lastError, testErrorLog, projectId, uid, generatedFiles }) {
  const [showErrorLog, setShowErrorLog] = useState(false)
  const errorLogPreview = testErrorLog ? testErrorLog.slice(0, 500) : null
  const errorLogTruncated = testErrorLog && testErrorLog.length > 500

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
        <div className="card border-amber-200 bg-amber-50 space-y-2">
          <p className="text-xs font-semibold text-amber-800">Test warning</p>
          <p className="text-xs text-amber-700 break-words">{lastError}</p>
          {errorLogPreview && (
            <div className="mt-1 space-y-1">
              <pre className="text-xs text-amber-700 font-mono bg-amber-100 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
                {showErrorLog ? testErrorLog : errorLogPreview}
                {!showErrorLog && errorLogTruncated && '…'}
              </pre>
              {errorLogTruncated && (
                <button
                  onClick={() => setShowErrorLog((v) => !v)}
                  className="text-xs text-amber-600 underline"
                >
                  {showErrorLog ? 'Show less' : 'Show more'}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Download */}
      <div className="card space-y-3">
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Download your code</h3>
        <p className="text-sm text-slate-500 dark:text-slate-400">
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

      {/* Deployment panel (animated URL button inside) */}
      {projectId && <DeploymentPanel projectId={projectId} uid={uid} />}

      {/* Generated file tree */}
      {generatedFiles && Object.keys(generatedFiles).length > 0 && (
        <div className="card space-y-3">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Generated files</h3>
          <GeneratedFileTree files={generatedFiles} />
        </div>
      )}

    </div>
  )
}

function ErrorScreen({ errorMessage, projectId }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const retryMut = useMutation({
    mutationFn: () => startGeneration(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (err) => {
      if (err.response?.status === 409) {
        const detail = err.response?.data?.detail
        if (detail?.code === 'another_generation_running') {
          toast.error(
            `Wait for "${detail.blocking_project_name}" to finish first.`,
            {
              action: {
                label: 'View running',
                onClick: () => navigate(`/projects/${detail.blocking_project_id}/generation`),
              },
            },
          )
        } else if (typeof detail === 'string' && detail.includes('in progress')) {
          toast.error('Generation is already running for this project.')
        } else {
          toast.error("This project isn't in a state where generation can start.")
        }
      } else {
        toast.error(err.friendlyMessage || err.message || 'Retry failed.')
      }
    },
  })

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

      <p className="text-sm text-slate-600 dark:text-slate-400">
        Something went wrong during code generation. Retry starts fresh from cycle 1
        — your requirements and blueprint are preserved.
      </p>

      <button
        className="btn-primary bg-red-600 hover:bg-red-700"
        onClick={() => retryMut.mutate()}
        disabled={retryMut.isPending}
      >
        {retryMut.isPending ? 'Starting…' : 'Retry Generation'}
      </button>
    </div>
  )
}

function RecoverableFailureScreen({ projectId, lastError, realFailures, testErrorLog }) {
  const qc = useQueryClient()

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
    qc.invalidateQueries({ queryKey: ['projects'] })
  }

  const rerunWithLlm = useMutation({
    mutationFn: () => rerunTests(projectId, true),
    onSuccess: invalidate,
    onError: (err) => toast.error(err.response?.data?.detail || err.message || 'Re-run failed.'),
  })

  const rerunAlgoOnly = useMutation({
    mutationFn: () => rerunTests(projectId, false),
    onSuccess: invalidate,
    onError: (err) => toast.error(err.response?.data?.detail || err.message || 'Re-run failed.'),
  })

  const regenerateMut = useMutation({
    mutationFn: () => startGeneration(projectId),
    onSuccess: invalidate,
    onError: (err) => toast.error(err.response?.data?.detail || err.message || 'Regeneration failed.'),
  })

  const anyPending = rerunWithLlm.isPending || rerunAlgoOnly.isPending || regenerateMut.isPending

  return (
    <div className="card border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 space-y-4">
      <div className="flex items-center gap-2">
        <AlertCircle size={20} className="text-amber-600 flex-shrink-0" />
        <h2 className="text-sm font-semibold text-amber-800 dark:text-amber-300">
          Tests Did Not Pass — Files Intact
        </h2>
      </div>

      {realFailures.length > 0 && (
        <div>
          <p className="text-xs text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1.5">
            Failed checks
          </p>
          <div className="flex flex-wrap gap-1.5">
            {realFailures.map((check) => (
              <span
                key={check}
                className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
              >
                {check}
              </span>
            ))}
          </div>
        </div>
      )}

      {lastError && (
        <div className="bg-amber-100 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
          <p className="text-xs text-amber-800 dark:text-amber-200 break-words">{lastError}</p>
        </div>
      )}

      {testErrorLog && (
        <details className="group">
          <summary className="text-xs text-slate-500 dark:text-slate-400 cursor-pointer select-none hover:text-slate-700 dark:hover:text-slate-300">
            Show error log
          </summary>
          <pre className="mt-2 text-xs font-mono bg-slate-900 text-slate-100 rounded p-3 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
            {testErrorLog}
          </pre>
        </details>
      )}

      <p className="text-sm text-slate-600 dark:text-slate-400">
        Your generated files are preserved. Choose how to proceed:
      </p>

      <div className="flex flex-col gap-2">
        <button
          className="btn-primary w-full"
          onClick={() => rerunWithLlm.mutate()}
          disabled={anyPending}
        >
          {rerunWithLlm.isPending ? 'Starting…' : 'Re-run Tests'}
        </button>

        <button
          className="btn-secondary w-full"
          onClick={() => rerunAlgoOnly.mutate()}
          disabled={anyPending}
          title="Uses deterministic fixes only — no Claude API calls"
        >
          {rerunAlgoOnly.isPending ? 'Starting…' : 'Re-run Tests (algorithmic only)'}
        </button>

        <button
          className="btn-outline w-full text-slate-600 dark:text-slate-400"
          onClick={() => regenerateMut.mutate()}
          disabled={anyPending}
        >
          {regenerateMut.isPending ? 'Starting…' : 'Regenerate from Scratch'}
        </button>
      </div>
    </div>
  )
}

// ── Checklist panel ──────────────────────────────────────────────────────────

const CHECKLIST_TYPE_LABELS = {
  model: 'Models',
  route: 'Routes',
  page: 'Pages',
  seed: 'Seed Data',
  style: 'Styles',
}

function ChecklistItemIcon({ status }) {
  if (status === 'pass') return <CheckCircle2 size={14} className="text-emerald-500 flex-shrink-0" />
  if (status === 'fail') return <XCircle size={14} className="text-red-500 flex-shrink-0" />
  if (status === 'stubbed') return <AlertTriangle size={14} className="text-yellow-500 flex-shrink-0" />
  // pending or unknown
  return <span className="w-3.5 h-3.5 flex-shrink-0 text-slate-300 text-xs leading-none">o</span>
}

function ChecklistItemContent({ item }) {
  if (item.type === 'route') {
    return (
      <span className="flex items-center gap-1 text-xs font-mono">
        <span className="text-accent">{item.method}</span>
        <span className="text-slate-600 dark:text-slate-400">{item.path}</span>
        {item.auth_required && <Lock size={10} className="text-slate-400" />}
      </span>
    )
  }
  if (item.type === 'page') {
    return (
      <span className="flex items-center gap-1 text-xs">
        <span className="font-mono text-slate-600 dark:text-slate-400">{item.route}</span>
        {item.nav_label && <span className="text-slate-400">{item.nav_label}</span>}
        {item.requires_auth && <Lock size={10} className="text-slate-400" />}
      </span>
    )
  }
  if (item.type === 'model') {
    return (
      <span className="text-xs text-slate-700 dark:text-slate-300">
        {item.class_name}
        {item.columns?.length > 0 && (
          <span className="text-slate-400"> ({item.columns.length} cols)</span>
        )}
      </span>
    )
  }
  if (item.type === 'seed') {
    return (
      <span className="text-xs text-slate-700 dark:text-slate-300">
        {item.model}
        <span className="text-slate-400"> ({item.count} rows)</span>
      </span>
    )
  }
  if (item.type === 'style') {
    return (
      <span className="text-xs font-mono text-slate-600 dark:text-slate-400">
        {item.css_var}: {item.value}
      </span>
    )
  }
  return <span className="text-xs text-slate-500">{item.id}</span>
}

function ChecklistPanel({ checklist, checklistResults, projectId }) {
  const resultsByItemId = {}
  for (const r of (checklistResults || [])) {
    resultsByItemId[r.item_id] = r
  }

  // Group items by type
  const groups = {}
  for (const item of checklist) {
    const t = item.type || 'unknown'
    if (!groups[t]) groups[t] = []
    groups[t].push(item)
  }

  const typeOrder = ['model', 'route', 'page', 'seed', 'style']
  const orderedGroups = typeOrder
    .filter((t) => groups[t])
    .map((t) => [t, groups[t]])

  return (
    <div className="card space-y-2">
      <CollapsibleSection
        title={`Checklist (${checklist.length} items)`}
        storageKey={`checklist-panel-${projectId}`}
        defaultOpen={false}
      >
        <div className="space-y-4">
          {orderedGroups.map(([type, items]) => (
            <div key={type}>
              <p className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1.5">
                {CHECKLIST_TYPE_LABELS[type] || type} ({items.length})
              </p>
              <ul className="space-y-1">
                {items.map((item) => {
                  const result = resultsByItemId[item.id]
                  const resultStatus = result?.status || 'pending'
                  return (
                    <li key={item.id} className="flex items-center gap-2">
                      <ChecklistItemIcon status={resultStatus} />
                      <ChecklistItemContent item={item} />
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>
      </CollapsibleSection>
    </div>
  )
}

// ── Stop + Restart buttons ────────────────────────────────────────────────────

function StopButton({ projectId, status }) {
  const [stopping, setStopping] = useState(false)
  const qc = useQueryClient()
  const inProgress = ['generating', 'testing', 'debugging'].includes(status)
  if (!inProgress) return null

  const handleStop = async () => {
    setStopping(true)
    try {
      await stopGeneration(projectId)
      qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
    } catch {
      setStopping(false)
      toast.error('Failed to request stop')
    }
  }

  return (
    <button
      className="btn-secondary text-red-600 border-red-200 hover:bg-red-50 text-sm"
      onClick={handleStop}
      disabled={stopping}
    >
      {stopping ? 'Stopping...' : 'Stop Generation'}
    </button>
  )
}

function RestartFromScratchButton({ projectId, status }) {
  const [restarting, setRestarting] = useState(false)
  const qc = useQueryClient()
  if (status !== 'stopped') return null

  const handleRestart = async () => {
    setRestarting(true)
    try {
      await restartFromScratch(projectId)
      qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    } catch {
      setRestarting(false)
      toast.error('Failed to restart')
    }
  }

  return (
    <button
      className="btn-primary text-sm"
      onClick={handleRestart}
      disabled={restarting}
    >
      {restarting ? 'Starting...' : 'Start Code Generation from Scratch'}
    </button>
  )
}

// ── Blueprint / requirements collapsible panel ─────────────────────────────

function CollapsibleSection({ title, children, defaultOpen = false, storageKey }) {
  const saved = storageKey ? sessionStorage.getItem(storageKey) : null
  const [open, setOpen] = useState(saved !== null ? saved === 'true' : defaultOpen)

  function toggle() {
    const next = !open
    setOpen(next)
    if (storageKey) sessionStorage.setItem(storageKey, String(next))
  }

  return (
    <div className="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors text-left"
      >
        <span className="text-sm font-medium text-slate-700 dark:text-slate-300">{title}</span>
        {open
          ? <ChevronDown size={16} className="text-slate-400" />
          : <ChevronRight size={16} className="text-slate-400" />}
      </button>
      {open && (
        <div className="px-4 py-3 bg-white dark:bg-slate-900 text-sm text-slate-700 dark:text-slate-300 space-y-2">
          {children}
        </div>
      )}
    </div>
  )
}

function BlueprintPanel({ blueprint, summary, structuredRequirements, projectId }) {
  const sk = (key) => `blueprint-panel-${projectId}-${key}`

  const entities = blueprint?.entities || []
  const routes = blueprint?.api_routes || blueprint?.api?.routes || blueprint?.routes || []
  const pages = blueprint?.frontend?.pages || blueprint?.pages || []

  return (
    <div className="card space-y-2">
      <div className="flex items-center gap-2 mb-1">
        <Code2 size={16} className="text-primary-600" />
        <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
          What the generator is building
        </h2>
      </div>

      {/* Requirements */}
      {structuredRequirements?.user_requirements?.length > 0 && (
        <CollapsibleSection
          title={`Approved requirements (${structuredRequirements.user_requirements.length})`}
          storageKey={sk('requirements')}
        >
          <ul className="space-y-1.5">
            {structuredRequirements.user_requirements.map((r, i) => (
              <li key={r.id || i} className="flex gap-2">
                {r.id && (
                  <span className="shrink-0 text-xs font-mono text-slate-400 mt-0.5">{r.id}</span>
                )}
                <span className="text-xs leading-relaxed">
                  {r.statement}
                  {r.acceptance_criteria?.length > 0 && (
                    <span className="block text-slate-400 mt-0.5">
                      ✓ {r.acceptance_criteria.join(' · ')}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {/* Summary */}
      {summary && (
        <CollapsibleSection title="Summary" storageKey={sk('summary')}>
          <p className="text-xs leading-relaxed whitespace-pre-line">{summary}</p>
        </CollapsibleSection>
      )}

      {/* Blueprint */}
      {(entities.length > 0 || routes.length > 0 || pages.length > 0) && (
        <CollapsibleSection title="Blueprint" storageKey={sk('blueprint')}>
          <div className="space-y-4">
            {entities.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1.5">
                  Data model
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr className="text-left border-b border-slate-200 dark:border-slate-700">
                        <th className="pb-1 pr-3 font-semibold">Entity</th>
                        <th className="pb-1 font-semibold">Fields</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entities.map((e, i) => (
                        <tr key={i} className="border-b border-slate-100 dark:border-slate-800">
                          <td className="py-1 pr-3 font-medium whitespace-nowrap">{e.name}</td>
                          <td className="py-1 text-slate-500 dark:text-slate-400">
                            {Array.isArray(e.fields) ? e.fields.join(', ') : e.fields || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {routes.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1.5">
                  API endpoints
                </p>
                <ul className="space-y-0.5">
                  {routes.map((r, i) => (
                    <li key={i} className="flex gap-2 text-xs">
                      <span className="shrink-0 font-mono text-accent">
                        {r.method || 'GET'}
                      </span>
                      <span className="font-mono text-slate-600 dark:text-slate-400 shrink-0">
                        {r.path}
                      </span>
                      {r.description && (
                        <span className="text-slate-400">— {r.description}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {pages.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1.5">
                  Pages
                </p>
                <ul className="space-y-0.5">
                  {pages.map((p, i) => (
                    <li key={i} className="flex gap-2 text-xs">
                      <span className="font-mono text-slate-600 dark:text-slate-400 shrink-0">
                        {p.route || p.path}
                      </span>
                      <span>{p.name || p.component}</span>
                      {p.auth_required && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                          auth
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </CollapsibleSection>
      )}
    </div>
  )
}
