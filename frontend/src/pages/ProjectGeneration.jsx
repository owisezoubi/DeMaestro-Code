import { useState, useRef, useEffect } from 'react'
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
  Trash2,
  RefreshCw,
  Sparkles,
  FolderTree,
  MessageSquareQuote,
  Circle,
  RotateCcw,
} from 'lucide-react'

import { getGenerationStatus, startGeneration, rerunTests, stopGeneration, restartFromScratch } from '../api/generation'
import { deleteProjectAndPurge } from '../lib/deleteWithCachePurge'
import DeploymentPanel from '../components/DeploymentPanel'
import GeneratedFileTree from '../components/GeneratedFileTree'
import GenerationAmbience from '../components/GenerationAmbience'
import EntertainingLoader from '../components/EntertainingLoader'

// Ordered pipeline stages
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
const DANGER_STATUSES = new Set(['ready', 'ready_with_warnings', 'deployed', 'failed', 'tests_failed_recoverable', 'stopped'])

function getStageState(stageIdx, currentStatus) {
  if (currentStatus === 'deployed') return 'completed'
  if ((currentStatus === 'ready' || currentStatus === 'ready_with_warnings') && stageIdx < 4) return 'completed'
  if ((currentStatus === 'ready' || currentStatus === 'ready_with_warnings') && stageIdx === 4) return 'active'
  if (currentStatus === 'failed') return 'failed'
  if (currentStatus === 'tests_failed_recoverable') {
    if (stageIdx === 0) return 'completed'
    if (stageIdx === 1) return 'failed'
    return 'pending'
  }
  const currentStageIdx = STATUS_TO_STAGE[currentStatus] ?? -1
  if (stageIdx < currentStageIdx) return 'completed'
  if (stageIdx === currentStageIdx) return 'active'
  return 'pending'
}

// ── Summary markdown parser ────────────────────────────────────────────────────

function parseSummary(md) {
  if (!md || typeof md !== 'string') return null
  const lines = md.split('\n')
  const sections = []
  let title = null
  let currentSection = null
  let currentParagraph = []

  const flushParagraph = () => {
    if (currentParagraph.length && currentSection) {
      currentSection.blocks.push({ kind: 'p', text: currentParagraph.join(' ').trim() })
      currentParagraph = []
    }
  }
  const flushSection = () => {
    flushParagraph()
    if (currentSection) sections.push(currentSection)
  }

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, '')
    const trimmed = line.trim()
    if (/^#\s+/.test(trimmed) && !title) {
      title = trimmed.replace(/^#\s+/, '')
      continue
    }
    if (/^##\s+/.test(trimmed)) {
      flushSection()
      currentSection = { heading: trimmed.replace(/^##\s+/, ''), blocks: [] }
      continue
    }
    if (/^###\s+/.test(trimmed)) {
      flushParagraph()
      if (!currentSection) currentSection = { heading: null, blocks: [] }
      currentSection.blocks.push({ kind: 'sub', text: trimmed.replace(/^###\s+/, '') })
      continue
    }
    if (/^[-*]\s+/.test(trimmed)) {
      flushParagraph()
      if (!currentSection) currentSection = { heading: null, blocks: [] }
      const last = currentSection.blocks[currentSection.blocks.length - 1]
      if (last && last.kind === 'ul') {
        last.items.push(trimmed.replace(/^[-*]\s+/, ''))
      } else {
        currentSection.blocks.push({ kind: 'ul', items: [trimmed.replace(/^[-*]\s+/, '')] })
      }
      continue
    }
    if (!trimmed) { flushParagraph(); continue }
    if (!currentSection) currentSection = { heading: null, blocks: [] }
    currentParagraph.push(trimmed)
  }
  flushSection()
  return { title, sections }
}

function SummaryRendered({ text }) {
  const parsed = parseSummary(text)
  if (!parsed) return null
  return (
    <div className="space-y-5">
      {parsed.title && (
        <div className="pb-2 border-b border-surface-border">
          <h3 className="text-xl font-black text-text-default leading-tight">{parsed.title}</h3>
        </div>
      )}
      {parsed.sections.map((sec, i) => (
        <div key={i} className="space-y-2">
          {sec.heading && (
            <p className="text-[10px] font-bold uppercase tracking-widest text-accent">{sec.heading}</p>
          )}
          {sec.blocks.map((b, j) => {
            if (b.kind === 'sub') {
              return <p key={j} className="text-sm font-semibold text-text-default">{b.text}</p>
            }
            if (b.kind === 'ul') {
              return (
                <ul key={j} className="space-y-1.5">
                  {b.items.map((item, k) => (
                    <li key={k} className="flex items-start gap-2 text-sm text-text-default/85">
                      <span className="mt-1.5 flex-shrink-0 w-1.5 h-1.5 rounded-full bg-accent" />
                      <span className="leading-relaxed">{item}</span>
                    </li>
                  ))}
                </ul>
              )
            }
            return <p key={j} className="text-sm text-text-default/85 leading-relaxed">{b.text}</p>
          })}
        </div>
      ))}
    </div>
  )
}

function InitialRequestPanel({ text }) {
  if (!text || !text.trim()) return null
  return (
    <PremiumCard>
      <div className="flex items-center gap-2 mb-3">
        <MessageSquareQuote className="w-4 h-4 text-accent" />
        <h2 className="text-sm font-bold text-text-default uppercase tracking-widest">Your request</h2>
      </div>
      <div className="relative pl-4 border-l-2 border-accent/40">
        <p className="text-sm text-text-default/85 leading-relaxed italic whitespace-pre-line">
          "{text.trim()}"
        </p>
        <p className="text-xs text-text-muted mt-2">What you typed in when you created this project.</p>
      </div>
    </PremiumCard>
  )
}

// Premium card wrapper
function PremiumCard({ children, className = '' }) {
  return (
    <div className={`relative rounded-2xl border border-surface-border
                     bg-surface-panel/80 backdrop-blur-sm shadow-sm
                     shadow-black/5 overflow-hidden ${className}`}>
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r
                      from-transparent via-accent/40 to-transparent" />
      <div className="p-6">{children}</div>
    </div>
  )
}

export default function ProjectGeneration() {
  const { id: projectId } = useParams()
  const { user } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: status, isLoading, error, isFetching } = useQuery({
    queryKey: ['generationStatus', projectId],
    queryFn: () => getGenerationStatus(projectId),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      if (TERMINAL_STATUSES.has(s)) return false
      return 2000
    },
    retry: 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  })

  const CONSECUTIVE_FAIL_THRESHOLD = 5
  const failCount = useRef(0)

  useEffect(() => {
    if (error) failCount.current += 1
    else if (status) failCount.current = 0
  }, [error, status])

  // Recovery: if we landed here with status 'approved' but generation never
  // started (e.g. wifi dropped right after approval), kick it off once.
  // 409 means already running — silently ignore.
  const resumedRef = useRef(false)
  useEffect(() => {
    if (!status || resumedRef.current) return
    if (status.status === 'approved') {
      resumedRef.current = true
      startGeneration(projectId)
        .then(() => {
          toast.info('Resuming generation…')
          qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
        })
        .catch((err) => {
          if (err.response?.status !== 409) {
            console.warn('Auto-resume failed', err)
          }
        })
    }
  }, [status?.status, projectId, qc])

  if (isLoading) {
    return (
      <div className="min-h-screen bg-surface-page flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-accent" />
      </div>
    )
  }

  if (error && failCount.current >= CONSECUTIVE_FAIL_THRESHOLD) {
    return (
      <div className="min-h-screen bg-surface-page flex items-center justify-center">
        <div className="rounded-xl border border-error/30 bg-error/5 p-6 w-96">
          <h2 className="text-base font-semibold text-error mb-2">Can't reach the backend</h2>
          <p className="text-sm text-error/80 mb-4">
            The generation server has been unreachable for {CONSECUTIVE_FAIL_THRESHOLD * 2}
            {' '}seconds. This usually means the backend crashed or restarted.
          </p>
          <button className="btn-primary w-full" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>
    )
  }

  const currentStatus = status?.status ?? 'generating'
  const isDone = DONE_STATUSES.has(currentStatus)
  const isFailed = currentStatus === 'failed'
  const isRecoverable = currentStatus === 'tests_failed_recoverable'
  const isStopped = currentStatus === 'stopped'
  const isInProgress = !isDone && !isFailed && !isRecoverable && !isStopped
  const showDangerZone = DANGER_STATUSES.has(currentStatus)

  // Compute progress pct for EntertainingLoader
  const currentStageIdx = STATUS_TO_STAGE[currentStatus] ?? 0
  const progressPct = status?.total_files > 0
    ? (((status.generated_count ?? 0) / status.total_files) * 100)
    : (((currentStageIdx + 0.5) / STAGES.length) * 100)
  const currentStage = STAGES[Math.max(0, currentStageIdx)]

  return (
    <div className="relative min-h-screen bg-surface-page">
      <GenerationAmbience />

      {error && failCount.current < CONSECUTIVE_FAIL_THRESHOLD && (
        <div className="fixed top-20 right-4 z-40 animate-fade-in">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full
                          bg-amber-500/10 border border-amber-500/40
                          text-amber-600 dark:text-amber-400 text-xs font-medium
                          shadow-lg backdrop-blur">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Reconnecting…
          </div>
        </div>
      )}

      <main className="max-w-5xl mx-auto px-4 py-8 space-y-6">
        {/* Original user request */}
        {status?.initial_request && (
          <InitialRequestPanel text={status.initial_request} />
        )}

        {/* Blueprint / requirements panel */}
        {(status?.blueprint || status?.structured_requirements || status?.summary) && (
          <PremiumCard>
            <BlueprintPanel
              blueprint={status.blueprint}
              summary={status.summary}
              structuredRequirements={status.structured_requirements}
              projectId={projectId}
            />
          </PremiumCard>
        )}

        {/* Checklist panel */}
        {status?.checklist?.length > 0 && (
          <PremiumCard>
            <ChecklistPanel
              checklist={status.checklist}
              checklistResults={status.checklist_results || []}
              projectId={projectId}
            />
          </PremiumCard>
        )}

        {/* Persistent file explorer — visible whenever files exist */}
        {status?.generated_files && Object.keys(status.generated_files).length > 0 && (
          <PremiumCard>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <FolderTree className="w-4 h-4 text-accent" />
                <h2 className="text-sm font-semibold text-text-default">
                  Project Explorer
                </h2>
                <span className="text-xs text-text-muted">
                  ({Object.keys(status.generated_files).length} files)
                </span>
              </div>
              <a
                href={`/api/projects/${projectId}/zip`}
                download
                className="text-xs text-accent hover:underline"
              >
                Download all
              </a>
            </div>
            <GeneratedFileTree files={status.generated_files} />
          </PremiumCard>
        )}

        {/* Page title */}
        <div className="text-center">
          <h1 className="text-2xl font-semibold text-text-default mb-1">
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
          <p className="text-sm text-text-muted">
            {isDone && 'Your application has been generated and packaged.'}
            {isFailed && 'Something went wrong. See the error below.'}
            {isRecoverable && 'Your files are intact — re-run tests or regenerate from scratch.'}
            {isStopped && 'Generation was stopped. You can restart from scratch.'}
            {isInProgress && 'Hang tight — Claude is writing your complete application...'}
          </p>
        </div>

        {/* Pipeline timeline */}
        <PremiumCard>
          <div className="flex items-start justify-between gap-2">
            {STAGES.map((stage, idx) => {
              const state = getStageState(idx, currentStatus)
              const Icon = stage.icon
              const isLast = idx === STAGES.length - 1

              return (
                <div key={stage.id} className="flex flex-col items-center flex-1 relative">
                  {!isLast && (
                    <div
                      className={`absolute top-6 left-1/2 w-full h-0.5 transition-colors duration-500 ${
                        state === 'completed' ? 'bg-emerald-400' : 'bg-surface-border'
                      }`}
                    />
                  )}

                  <div
                    className={`relative z-10 w-12 h-12 rounded-full flex items-center justify-center mb-2 transition-all duration-300 ${
                      state === 'completed'
                        ? 'bg-emerald-100 text-emerald-600 ring-2 ring-emerald-300 dark:bg-emerald-900/30 dark:text-emerald-400'
                        : state === 'active'
                        ? 'bg-accent/15 text-accent ring-2 ring-accent/40 animate-pulse shadow-lg shadow-accent/20'
                        : state === 'failed'
                        ? 'bg-error/10 text-error'
                        : 'bg-surface-border/50 text-text-muted'
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
                      state === 'active' ? 'text-accent' : 'text-text-muted'
                    }`}
                  >
                    {stage.label}
                  </p>
                  <p className="text-xs text-text-muted text-center mt-0.5 hidden sm:block">
                    {stage.description}
                  </p>
                </div>
              )
            })}
          </div>
        </PremiumCard>

        {/* In-progress: EntertainingLoader + activity card */}
        {isInProgress && (
          <PremiumCard>
            <div className="flex items-center justify-between gap-2 mb-4">
              <h2 className="text-sm font-semibold text-text-default">Current Activity</h2>
              <StopButton projectId={projectId} status={currentStatus} />
            </div>

            <EntertainingLoader
              progressPct={progressPct}
              stageLabel={currentStage?.label || 'Working'}
              stageIcon={currentStage?.icon}
            />

            {/* Test results checks */}
            {status?.test_results?.passed_checks && (
              <div className="mt-4">
                <p className="text-xs text-text-muted uppercase tracking-wide mb-2">Test Results</p>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(status.test_results.passed_checks).map(([check, passed]) => (
                    <div key={check} className="flex items-center gap-1.5">
                      {passed ? (
                        <CheckCircle2 size={14} className="text-emerald-500 flex-shrink-0" />
                      ) : (
                        <AlertCircle size={14} className="text-error flex-shrink-0" />
                      )}
                      <span className="text-xs text-text-muted capitalize">{check}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </PremiumCard>
        )}

        {/* Success screen */}
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

        {/* Error screen */}
        {isFailed && (
          <PremiumCard>
            <ErrorScreen errorMessage={status.error_message} projectId={projectId} />
          </PremiumCard>
        )}

        {isRecoverable && (
          <PremiumCard>
            <RecoverableFailureScreen
              projectId={projectId}
              lastError={status.last_error}
              realFailures={status.real_failures || []}
              testErrorLog={status.test_error_log}
            />
          </PremiumCard>
        )}

        {/* Stopped screen */}
        {isStopped && (
          <PremiumCard>
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <AlertCircle size={20} className="text-text-muted flex-shrink-0" />
                <h2 className="text-sm font-semibold text-text-default">Generation Stopped</h2>
              </div>
              <p className="text-sm text-text-muted">
                The generation pipeline was stopped by request. You can restart it from scratch to generate a new version.
              </p>
              <RestartFromScratchButton projectId={projectId} status={currentStatus} />
            </div>
          </PremiumCard>
        )}

        {/* Danger Zone */}
        {showDangerZone && (
          <DangerZone
            projectId={projectId}
            projectName={status?.name || 'this project'}
          />
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
      {/* 1. Success banner */}
      <div
        className={`rounded-2xl text-white p-8 text-center shadow-md ${
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
        <PremiumCard>
          <p className="text-xs font-semibold text-amber-800 dark:text-amber-300 mb-2">Test warning</p>
          <p className="text-xs text-amber-700 dark:text-amber-400 break-words">{lastError}</p>
          {errorLogPreview && (
            <div className="mt-2 space-y-1">
              <pre className="text-xs text-amber-700 dark:text-amber-300 font-mono bg-amber-100 dark:bg-amber-900/30 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
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
        </PremiumCard>
      )}

      {/* 2. Live URL (DeploymentPanel) */}
      {projectId && (
        <PremiumCard>
          <DeploymentPanel projectId={projectId} uid={uid} />
        </PremiumCard>
      )}

      {/* 3. Download ZIP */}
      <PremiumCard>
        <h3 className="text-sm font-semibold text-text-default mb-2">Download your code</h3>
        <p className="text-sm text-text-muted mb-4">
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
      </PremiumCard>

      {/* 4. Blueprint / Requirements (collapsible, defaultOpen=false) — rendered by parent via BlueprintPanel at top */}
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
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <AlertCircle size={20} className="text-error flex-shrink-0" />
        <h2 className="text-sm font-semibold text-error">Generation Failed</h2>
      </div>

      {errorMessage && (
        <div className="bg-error/5 border border-error/20 rounded-lg p-3">
          <p className="text-xs font-mono text-error/80 break-words">{errorMessage}</p>
        </div>
      )}

      <p className="text-sm text-text-muted">
        Something went wrong during code generation. Retry starts fresh from cycle 1
        — your requirements and blueprint are preserved.
      </p>

      <button
        className="btn-primary bg-red-600 hover:bg-red-700 w-full"
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

  const regenerateMut = useMutation({
    mutationFn: () => startGeneration(projectId),
    onSuccess: invalidate,
    onError: (err) => toast.error(err.response?.data?.detail || err.message || 'Regeneration failed.'),
  })

  const anyPending = rerunWithLlm.isPending || regenerateMut.isPending

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <AlertCircle size={20} className="text-warning flex-shrink-0" />
        <h2 className="text-sm font-semibold text-warning">Tests Did Not Pass — Files Intact</h2>
      </div>

      {realFailures.length > 0 && (
        <div>
          <p className="text-xs text-text-muted uppercase tracking-wide mb-1.5">Failed checks</p>
          <div className="flex flex-wrap gap-1.5">
            {realFailures.map((check) => (
              <span
                key={check}
                className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-error/10 text-error"
              >
                {check}
              </span>
            ))}
          </div>
        </div>
      )}

      {lastError && (
        <div className="bg-warning/5 border border-warning/20 rounded-lg p-3">
          <p className="text-xs text-warning/80 break-words">{lastError}</p>
        </div>
      )}

      {testErrorLog && (
        <details className="group">
          <summary className="text-xs text-text-muted cursor-pointer select-none hover:text-text-default">
            Show error log
          </summary>
          <pre className="mt-2 text-xs font-mono bg-slate-900 text-slate-100 rounded p-3 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
            {testErrorLog}
          </pre>
        </details>
      )}

      <p className="text-sm text-text-muted">Your generated files are preserved. Choose how to proceed:</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {/* Primary: re-run tests with LLM */}
        <button
          onClick={() => rerunWithLlm.mutate()}
          disabled={anyPending}
          className="group relative overflow-hidden
                     px-5 py-3 rounded-xl font-semibold text-sm
                     bg-surface-panel border border-surface-border
                     text-text-default
                     hover:border-accent/50 hover:bg-accent/5
                     disabled:opacity-60 disabled:cursor-not-allowed
                     transition-all duration-200
                     flex items-center justify-center gap-2"
        >
          {rerunWithLlm.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Starting…
            </>
          ) : (
            <>
              <RefreshCw className="w-4 h-4" />
              Re-run Tests
            </>
          )}
        </button>

        {/* Decisive: regenerate from scratch */}
        <button
          onClick={() => regenerateMut.mutate()}
          disabled={anyPending}
          className="group relative overflow-hidden
                     px-5 py-3 rounded-xl font-bold text-sm
                     text-white
                     bg-gradient-to-r from-amber-500 via-orange-500 to-amber-500
                     bg-[length:200%_auto] animate-gradient
                     shadow-lg shadow-amber-500/30
                     hover:shadow-xl hover:shadow-amber-500/40
                     hover:scale-[1.02] active:scale-[0.98]
                     disabled:opacity-60 disabled:cursor-not-allowed disabled:scale-100
                     transition-all duration-200
                     flex items-center justify-center gap-2"
        >
          <span className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.25),transparent_60%)]
                           opacity-0 group-hover:opacity-100 transition-opacity" />
          {regenerateMut.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin relative z-10" />
              <span className="relative z-10">Regenerating…</span>
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4 relative z-10" />
              <span className="relative z-10">Regenerate from Scratch</span>
            </>
          )}
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
  if (status === 'partial') return <Circle size={14} className="text-emerald-500/60 flex-shrink-0" />
  if (status === 'fail') return <XCircle size={14} className="text-error flex-shrink-0" />
  if (status === 'stubbed') return <AlertTriangle size={14} className="text-warning flex-shrink-0" />
  return <span className="w-3.5 h-3.5 flex-shrink-0 text-text-muted text-xs leading-none">o</span>
}

function ChecklistItemContent({ item }) {
  if (item.type === 'route') {
    return (
      <span className="flex items-center gap-1 text-xs font-mono">
        <span className="text-accent">{item.method}</span>
        <span className="text-text-muted">{item.path}</span>
        {item.auth_required && <Lock size={10} className="text-text-muted" />}
      </span>
    )
  }
  if (item.type === 'page') {
    return (
      <span className="flex items-center gap-1 text-xs">
        <span className="font-mono text-text-muted">{item.route}</span>
        {item.nav_label && <span className="text-text-muted">{item.nav_label}</span>}
        {item.requires_auth && <Lock size={10} className="text-text-muted" />}
      </span>
    )
  }
  if (item.type === 'model') {
    return (
      <span className="text-xs text-text-default">
        {item.class_name}
        {item.columns?.length > 0 && (
          <span className="text-text-muted"> ({item.columns.length} cols)</span>
        )}
      </span>
    )
  }
  if (item.type === 'seed') {
    return (
      <span className="text-xs text-text-default">
        {item.model}
        <span className="text-text-muted"> ({item.count} rows)</span>
      </span>
    )
  }
  if (item.type === 'style') {
    return (
      <span className="text-xs font-mono text-text-muted">
        {item.css_var}: {item.value}
      </span>
    )
  }
  return <span className="text-xs text-text-muted">{item.id}</span>
}

function ChecklistPanel({ checklist, checklistResults, projectId }) {
  // Dedup by item_id — keep the latest entry per id (backend may emit duplicates
  // during live-refresh cycles, which would cause >100% without this).
  const dedupedResults = Array.from(
    new Map((checklistResults || []).map((r) => [r.item_id, r])).values()
  )

  // Only count results that reference actual items in the current checklist.
  const validItemIds = new Set(checklist.map((i) => i.id))
  const validResults = dedupedResults.filter((r) => validItemIds.has(r.item_id))

  const resultsByItemId = {}
  for (const r of validResults) {
    resultsByItemId[r.item_id] = r
  }

  const groups = {}
  for (const item of checklist) {
    const t = item.type || 'unknown'
    if (!groups[t]) groups[t] = []
    groups[t].push(item)
  }

  const typeOrder = ['model', 'route', 'page', 'seed', 'style']
  const orderedGroups = typeOrder.filter((t) => groups[t]).map((t) => [t, groups[t]])

  const passedCount = Math.min(
    validResults.filter((r) => r.status === 'pass' || r.status === 'partial').length,
    checklist.length,
  )
  const total = checklist.length
  const allDone = passedCount === total && total > 0
  const percent = total > 0 ? Math.min(100, Math.round((passedCount / total) * 100)) : 0

  return (
    <div className="space-y-2">
      <CollapsibleSection
        title={
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold">Checklist</span>
            <span
              className={`text-xs font-mono px-2 py-0.5 rounded-full
                         ${allDone
                           ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300 animate-pulse-slow'
                           : 'bg-accent/10 text-accent'}`}
            >
              {passedCount} / {total}
            </span>
            {allDone ? (
              <div className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 animate-fade-in">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span className="font-semibold">Everything built</span>
              </div>
            ) : (
              <div className="flex items-center gap-1.5 text-xs text-text-muted">
                <div className="w-16 h-1 rounded-full bg-surface-border overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-accent to-accent-secondary
                               transition-all duration-500 ease-out"
                    style={{ width: `${percent}%` }}
                  />
                </div>
                <span className="tabular-nums">{percent}%</span>
              </div>
            )}
          </div>
        }
        storageKey={`checklist-panel-${projectId}`}
        defaultOpen={false}
      >
        <div className="space-y-4">
          {orderedGroups.map(([type, items]) => (
            <div key={type}>
              <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1.5">
                {CHECKLIST_TYPE_LABELS[type] || type} ({items.length})
              </p>
              <ul className="space-y-1">
                {items.map((item) => {
                  const result = resultsByItemId[item.id]
                  const resultStatus = result?.status || 'pending'
                  return (
                    <li
                      key={item.id}
                      className={`flex items-center gap-2 transition-all duration-500
                                 ${resultStatus === 'pass' ? 'animate-checklist-pass' : ''}`}
                    >
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
      className="btn-secondary text-error border-error/30 hover:bg-error/5 text-sm"
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

// ── Collapsible + Blueprint ─────────────────────────────────────────────────

function CollapsibleSection({ title, children, defaultOpen = false, storageKey }) {
  const saved = storageKey ? sessionStorage.getItem(storageKey) : null
  const [open, setOpen] = useState(saved !== null ? saved === 'true' : defaultOpen)

  function toggle() {
    const next = !open
    setOpen(next)
    if (storageKey) sessionStorage.setItem(storageKey, String(next))
  }

  return (
    <div className="border border-surface-border rounded-lg overflow-hidden">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-3
                   bg-surface-page/50 hover:bg-surface-border/30 transition-colors text-left"
      >
        <span className="text-sm font-medium text-text-default">{title}</span>
        {open
          ? <ChevronDown size={16} className="text-text-muted" />
          : <ChevronRight size={16} className="text-text-muted" />}
      </button>
      {open && (
        <div className="px-4 py-3 bg-surface-page/30 text-sm text-text-default space-y-2">
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
  const pages = (
    blueprint?.frontend?.pages ||
    blueprint?.pages ||
    blueprint?.ui?.pages ||
    blueprint?.screens ||
    blueprint?.frontend_pages ||
    []
  )

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 mb-1">
        <Code2 size={16} className="text-accent" />
        <h2 className="text-sm font-semibold text-text-default">
          What the generator is building
        </h2>
      </div>

      {structuredRequirements?.user_requirements?.length > 0 && (
        <CollapsibleSection
          title={`Approved requirements (${structuredRequirements.user_requirements.length})`}
          storageKey={sk('requirements')}
        >
          <ul className="space-y-1.5">
            {structuredRequirements.user_requirements.map((r, i) => (
              <li key={r.id || i} className="flex gap-2">
                {r.id && (
                  <span className="shrink-0 text-xs font-mono text-text-muted mt-0.5">{r.id}</span>
                )}
                <span className="text-xs leading-relaxed">
                  {r.statement}
                  {r.acceptance_criteria?.length > 0 && (
                    <span className="block text-text-muted mt-0.5">
                      ✓ {r.acceptance_criteria.join(' · ')}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </CollapsibleSection>
      )}

      {summary && (
        <CollapsibleSection title="Summary" storageKey={sk('summary')} defaultOpen>
          <SummaryRendered text={summary} />
        </CollapsibleSection>
      )}

      {(entities.length > 0 || routes.length > 0 || pages.length > 0) && (
        <CollapsibleSection title="Blueprint" storageKey={sk('blueprint')}>
          <div className="space-y-4">
            {entities.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1.5">Data model</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr className="text-left border-b border-surface-border">
                        <th className="pb-1 pr-3 font-semibold">Entity</th>
                        <th className="pb-1 font-semibold">Fields</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entities.map((e, i) => (
                        <tr key={i} className="border-b border-surface-border/50">
                          <td className="py-1 pr-3 font-medium whitespace-nowrap">{e.name}</td>
                          <td className="py-1 text-text-muted">
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
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1.5">API endpoints</p>
                <ul className="space-y-0.5">
                  {routes.map((r, i) => (
                    <li key={i} className="flex gap-2 text-xs">
                      <span className="shrink-0 font-mono text-accent">{r.method || 'GET'}</span>
                      <span className="font-mono text-text-muted shrink-0">{r.path}</span>
                      {r.description && <span className="text-text-muted">— {r.description}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {pages.length > 0 && (
              <div>
                <p className="text-xs font-bold text-text-muted uppercase tracking-widest mb-2">
                  Pages that will be built ({pages.length})
                </p>
                <ul className="space-y-1.5">
                  {pages.map((p, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-3 p-2 rounded-lg
                                 bg-surface-page/40 border border-surface-border/60"
                    >
                      <div className="flex-shrink-0 w-6 h-6 rounded-md
                                      bg-gradient-to-br from-accent/20 to-accent-secondary/20
                                      flex items-center justify-center text-[10px] font-bold text-accent">
                        {i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-text-default">
                            {p.name || p.component || p.title || 'Page'}
                          </span>
                          {p.auth_required && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent uppercase font-bold tracking-widest">
                              auth
                            </span>
                          )}
                        </div>
                        <div className="text-xs font-mono text-text-muted truncate">
                          {p.route || p.path || '—'}
                        </div>
                        {(p.description || p.purpose) && (
                          <p className="text-xs text-text-muted mt-0.5 line-clamp-2">
                            {p.description || p.purpose}
                          </p>
                        )}
                      </div>
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

// ── Danger Zone ───────────────────────────────────────────────────────────────

function DangerZone({ projectId, projectName }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(null) // null | 'restart' | 'delete'

  const restartMutation = useMutation({
    mutationFn: () => restartFromScratch(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
      qc.invalidateQueries({ queryKey: ['deploymentStatus', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setConfirm(null)
      toast.success('Starting over from scratch')
    },
    onError: (err) => {
      const status = err.response?.status
      if (status === 409) {
        toast.error(err.response?.data?.detail || 'Wait for the current phase to finish.')
      } else {
        toast.error(err.friendlyMessage || err.message || 'Failed to restart')
      }
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteProjectAndPurge(qc, projectId),
    onSuccess: () => {
      toast.success('Project deleted')
      navigate('/dashboard')
    },
    onError: (err) => toast.error('Delete failed: ' + (err.message || 'Unknown error')),
  })

  const busy = restartMutation.isPending || deleteMutation.isPending

  return (
    <>
      <div className="relative rounded-2xl border border-error/30 bg-error/5 overflow-hidden">
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-error/40 to-transparent" />
        <div className="p-6">
          <h3 className="text-sm font-semibold text-error mb-1">Danger Zone</h3>
          <p className="text-xs text-text-muted mb-4">
            These actions cannot be undone.
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              onClick={() => setConfirm('restart')}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium
                         border border-amber-500/40 text-amber-500 hover:bg-amber-500/10 transition-colors"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Start from scratch
            </button>
            <button
              onClick={() => setConfirm('delete')}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium
                         border border-error/40 text-error hover:bg-error/10 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Delete project
            </button>
          </div>
        </div>
      </div>

      {confirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => { if (!busy) setConfirm(null) }}
        >
          <div
            className="bg-surface-panel rounded-xl shadow-xl p-6 max-w-sm w-full mx-4
                        border border-surface-border animate-scale-in"
            onClick={(e) => e.stopPropagation()}
          >
            {confirm === 'restart' ? (
              <>
                <h2 className="text-lg font-semibold text-text-default mb-2">
                  Start from scratch?
                </h2>
                <p className="text-sm text-text-muted mb-6">
                  This will regenerate the code for{' '}
                  <span className="font-semibold text-text-default">"{projectName}"</span>{' '}
                  from scratch. Your requirements and blueprint are kept — the code
                  will be rewritten, re-tested, and re-deployed. The current live
                  URL will be replaced by the new build (same URL, new content).
                </p>
                <div className="flex justify-end gap-3">
                  <button
                    onClick={() => setConfirm(null)}
                    disabled={busy}
                    className="btn-secondary disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => restartMutation.mutate()}
                    disabled={busy}
                    className="px-4 py-2 rounded-lg text-sm font-medium bg-amber-500
                               hover:bg-amber-600 text-white disabled:opacity-60
                               transition-colors inline-flex items-center gap-2"
                  >
                    {restartMutation.isPending ? (
                      <>
                        <span className="inline-block w-3.5 h-3.5 border-2 border-white
                                          border-t-transparent rounded-full animate-spin" />
                        Restarting…
                      </>
                    ) : (
                      <>
                        <RotateCcw className="w-3.5 h-3.5" />
                        Yes, restart
                      </>
                    )}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h2 className="text-lg font-semibold text-text-default mb-2">
                  Delete project?
                </h2>
                <p className="text-sm text-text-muted mb-6">
                  This will permanently delete all generated files, the deployment, and{' '}
                  <span className="font-medium text-text-default">{projectName}</span>.{' '}
                  This cannot be undone.
                </p>
                <div className="flex justify-end gap-3">
                  <button
                    onClick={() => setConfirm(null)}
                    disabled={busy}
                    className="btn-secondary disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate()}
                    disabled={busy}
                    className="px-4 py-2 rounded-lg text-sm font-medium bg-red-600
                               hover:bg-red-700 text-white disabled:opacity-60
                               transition-colors inline-flex items-center gap-2"
                  >
                    {deleteMutation.isPending ? (
                      <>
                        <span className="inline-block w-3.5 h-3.5 border-2 border-white
                                          border-t-transparent rounded-full animate-spin" />
                        Deleting…
                      </>
                    ) : (
                      'Delete project'
                    )}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
