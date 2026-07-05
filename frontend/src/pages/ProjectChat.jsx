import { useState, useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Loader2, CheckCircle2, AlertCircle } from 'lucide-react'

import { getProjectStatus } from '../api/requirements'
import { triggerStructuring, getClarifications, answerClarificationsBatch, getClarificationProgress, saveClarificationProgress } from '../api/structure'
import { getProject } from '../api/projects'
import { startGeneration } from '../api/generation'
import ClarificationLoader from '../components/ClarificationLoader'
import StructuringLoader from '../components/StructuringLoader'

const TERMINAL = new Set([
  'ready', 'ready_with_warnings', 'deployed',
  'failed', 'tests_failed_recoverable', 'stopped',
  'awaiting_approval',
])

const STATUS_STYLES = {
  awaiting_input:    'bg-slate-100 text-slate-700',
  structuring:       'bg-amber-100 text-amber-800',
  clarifying:        'bg-amber-100 text-amber-800',
  awaiting_approval: 'bg-orange-100 text-orange-800',
  blueprinting:      'bg-blue-100 text-blue-800',
  generating:        'bg-blue-100 text-blue-800',
  verifying:         'bg-blue-100 text-blue-800',
  packaging:         'bg-blue-100 text-blue-800',
  ready:             'bg-emerald-100 text-emerald-800',
  failed:            'bg-red-100 text-red-800',
}

const STATUS_DESC = {
  blueprinting: 'Designing the application blueprint…',
  generating:   'Generating your application code…',
  verifying:    'Verifying the generated code…',
  packaging:    'Packaging your application…',
  ready:        'Your application is ready!',
}

export default function ProjectChat() {
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: statusData } = useQuery({
    queryKey: ['project-status', projectId],
    queryFn: () => getProjectStatus(projectId),
    refetchInterval: (query) =>
      TERMINAL.has(query.state.data?.status) ? false : 2000,
  })

  const status = statusData?.status

  // Bounce any state that belongs to the generation page so we never get
  // stuck polling here. replace:true keeps the back button clean.
  useEffect(() => {
    if (!status) return
    const generationStates = new Set([
      'blueprinting', 'generating', 'generated', 'testing', 'tested',
      'verifying', 'verified', 'deploying', 'packaging',
      'ready', 'ready_with_warnings', 'deployed',
      'modifying', 'regenerating',
      'tests_failed_recoverable', 'failed', 'stopped',
    ])
    if (generationStates.has(status)) {
      navigate(`/projects/${projectId}/generation`, { replace: true })
    }
  }, [status, projectId, navigate])

  const { data: clarifications = [], isLoading: clarificationsLoading } = useQuery({
    queryKey: ['clarifications', projectId],
    queryFn: () => getClarifications(projectId),
    refetchInterval: () => (status === 'clarifying' ? 3000 : false),
    enabled: status === 'clarifying',
  })

  useEffect(() => {
    if (status === 'clarifying') {
      console.log('[Clarifications] received', clarifications?.length, 'items, first id:', clarifications?.[0]?.id)
    }
  }, [clarifications, status])

  useEffect(() => {
    return () => {
      qc.cancelQueries({ queryKey: ['clarifications', projectId] })
    }
  }, [projectId, qc])

  const isMigratingAway = status && [
    'blueprinting', 'generating', 'generated', 'testing', 'tested',
    'verifying', 'verified', 'deploying', 'packaging',
    'ready', 'ready_with_warnings', 'deployed',
    'modifying', 'regenerating',
    'tests_failed_recoverable', 'failed', 'stopped',
  ].includes(status)

  if (isMigratingAway) {
    return (
      <div className="min-h-screen bg-surface-page flex items-center justify-center">
        <div className="flex items-center gap-3 text-text-muted">
          <Loader2 className="w-5 h-5 animate-spin text-accent" />
          <span className="text-sm">Loading project…</span>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-surface-page relative overflow-x-hidden">
      {/* Ambient page blobs */}
      <div className="fixed top-1/4 -left-40 w-96 h-96 rounded-full
                      bg-accent/[0.08] blur-3xl pointer-events-none" />
      <div className="fixed bottom-1/4 -right-40 w-96 h-96 rounded-full
                      bg-accent-secondary/[0.08] blur-3xl pointer-events-none" />

      {status && (
        <div className="max-w-3xl mx-auto px-4 pt-4">
          <span
            className={`text-xs px-2.5 py-1 rounded-full font-medium ${STATUS_STYLES[status] ?? 'bg-slate-100 text-slate-700'}`}
          >
            {status.replace(/_/g, ' ')}
          </span>
        </div>
      )}

      <main className="max-w-3xl mx-auto px-4 py-6">
        <MainContent
          status={status}
          projectId={projectId}
          clarifications={clarifications}
          clarificationsLoading={clarificationsLoading}
          qc={qc}
          navigate={navigate}
        />
      </main>
    </div>
  )
}

function MainContent({ status, projectId, clarifications, clarificationsLoading, qc, navigate }) {
  if (!status) {
    return (
      <div className="card flex items-center gap-3 text-slate-500 dark:text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Loading…</span>
      </div>
    )
  }

  if (status === 'awaiting_input') {
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <p className="text-slate-500 dark:text-slate-400 mb-4">No requirements submitted yet.</p>
        <Link to="/dashboard" className="btn-secondary">
          Go to dashboard
        </Link>
      </div>
    )
  }

  if (status === 'structuring') {
    return <StructuringLoader />
  }

  if (status === 'clarifying') {
    return (
      <ClarificationWizard
        key={clarifications.map((c) => c.id).join('|') || 'none'}
        clarifications={clarifications}
        projectId={projectId}
        qc={qc}
      />
    )
  }

  if (status === 'awaiting_approval') {
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <CheckCircle2 className="w-10 h-10 text-emerald-500 mb-4" />
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-1">
          Your requirements are ready!
        </h2>
        <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
          We have everything we need to move forward.
        </p>
        <button
          className="btn-primary"
          onClick={() => navigate(`/projects/${projectId}/approve`)}
        >
          Review and approve →
        </button>
      </div>
    )
  }

  if (status === 'failed') {
    return <FailedCard projectId={projectId} qc={qc} />
  }

  // blueprinting, generating, verifying, packaging, ready — future weeks
  return (
    <div className="card">
      <p className="font-medium text-slate-900 dark:text-slate-100">Status: {status.replace(/_/g, ' ')}</p>
      <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
        {STATUS_DESC[status] ?? 'Processing…'}
      </p>
    </div>
  )
}

const GENERATION_STAGES = new Set([
  'architect', 'generating', 'generated', 'debugging',
  'testing', 'tested', 'verifying', 'verified', 'packaging',
])

function FailedCard({ projectId, qc }) {
  const navigate = useNavigate()
  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => getProject(projectId),
  })

  const stage = project?.current_stage || ''
  const errorMessage = project?.error_message || project?.last_error || ''
  const isGenFailure = GENERATION_STAGES.has(stage)

  const retryMut = useMutation({
    mutationFn: () =>
      isGenFailure ? startGeneration(projectId) : triggerStructuring(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project-status', projectId] })
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      qc.invalidateQueries({ queryKey: ['generationStatus', projectId] })
      toast.success(
        isGenFailure
          ? 'Retrying code generation — starting fresh from cycle 1'
          : 'Re-analyzing your requirements…',
      )
      if (isGenFailure) {
        navigate(`/projects/${projectId}/generation`)
      }
    },
    onError: (err) => {
      if (err.response?.status === 409) {
        const detail = err.response?.data?.detail || ''
        if (detail.includes('in progress')) {
          toast.error('Generation is already running for this project.')
        } else {
          toast.error("This project isn't in a state where generation can start.")
        }
      } else {
        toast.error(err.friendlyMessage || 'Retry failed. Please try again.')
      }
    },
  })

  const title = isGenFailure ? 'Code generation failed' : 'Something went wrong'
  const partialCount = project?.generated_count || 0
  const totalCount = project?.total_files || 0
  const description = isGenFailure
    ? (partialCount > 0
        ? `Generation failed after ${partialCount}${totalCount ? ' of ' + totalCount : ''} files. Retry starts fresh from cycle 1 — your requirements and blueprint are saved.`
        : "Your approved requirements and blueprint are saved. Retry will start code generation from cycle 1.")
    : "We couldn't analyze your requirements. Retry will run the analysis again."
  const buttonText = isGenFailure ? 'Retry code generation' : 'Retry analysis'

  return (
    <div className="card flex flex-col items-center py-12 text-center">
      <AlertCircle className="w-10 h-10 text-red-500 mb-4" />
      <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-1">
        {title}
      </h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-4 max-w-md">
        {description}
      </p>
      {project?.last_failed_checks?.length > 0 && (
        <div className="mb-4 text-sm">
          <p className="font-semibold text-slate-800 dark:text-slate-100">Failed checks:</p>
          <ul className="list-disc list-inside text-slate-600 dark:text-slate-400">
            {project.last_failed_checks.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {errorMessage && (
        <div className="text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20
                        border border-red-200 dark:border-red-800 px-3 py-2 rounded-md mb-4
                        max-w-md text-left font-mono break-words">
          <span className="font-semibold not-italic">Previous error:</span><br />
          {errorMessage.length > 400 ? errorMessage.slice(0, 400) + '…' : errorMessage}
        </div>
      )}
      <button
        className="btn-primary"
        onClick={() => retryMut.mutate()}
        disabled={retryMut.isPending}
      >
        {retryMut.isPending ? 'Retrying…' : buttonText}
      </button>
    </div>
  )
}

const ENG_KEYWORDS = [
  'wcag', 'contrast ratio', 'api', 'endpoint', 'schema', 'entity',
  'model', 'framework', 'measurable criterion', 'authenticated',
  'unauthenticated', 'replace with', 'clarify whether', 'align all',
]

function isEngineeringLanguage(opt) {
  const t = (opt || '').toLowerCase()
  return ENG_KEYWORDS.some((k) => t.includes(k))
}

function ClarificationWizard({ clarifications, projectId, qc }) {
  const idsKey = clarifications.map((c) => c.id).join('|')

  const { data: savedProgress } = useQuery({
    queryKey: ['clarification-progress', projectId],
    queryFn: () => getClarificationProgress(projectId),
  })

  const [index, setIndex] = useState(0)
  const [answers, setAnswers] = useState({})
  const [freeText, setFreeText] = useState('')

  useEffect(() => {
    const data = savedProgress || {}
    const valid = Object.fromEntries(
      Object.entries(data).filter(
        ([id]) => clarifications.some((c) => c.id === id),
      ),
    )
    setAnswers(valid)
    const firstUnanswered = clarifications.findIndex((c) => !(c.id in valid))
    setIndex(firstUnanswered === -1
      ? Math.max(0, clarifications.length - 1)
      : firstUnanswered)
    setFreeText('')
  }, [idsKey, savedProgress])

  const submitMut = useMutation({
    mutationFn: (all) => answerClarificationsBatch(projectId, all),
    onSuccess: async () => {
      await Promise.all([
        qc.refetchQueries({ queryKey: ['project-status', projectId], type: 'all' }),
        qc.refetchQueries({ queryKey: ['clarifications', projectId], type: 'all' }),
      ])
    },
    onError: (err) => {
      const msg = err.friendlyMessage || err.message || ''
      const isTimeout = err.response?.status === 504 || err.code === 'ECONNABORTED' || msg.toLowerCase().includes('timeout')
      if (isTimeout) toast.info('Still refining — this can take a minute')
      else toast.error(msg || 'Failed to submit answers. Please try again.')
    },
  })

  if (submitMut.isPending) {
    return <ClarificationLoader mode="refine" />
  }

  if (!clarifications.length) {
    return <StructuringLoader />
  }

  const total = clarifications.length
  const current = clarifications[index]
  const isLast = index === total - 1

  function record(answer) {
    const next = { ...answers, [current.id]: answer }
    setAnswers(next)
    setFreeText('')
    saveClarificationProgress(projectId, next).catch(() => {})
    if (isLast) {
      submitMut.mutate(clarifications.map((c) => ({ ambiguity_id: c.id, answer: next[c.id] ?? '' })))
    } else {
      setIndex((i) => i + 1)
    }
  }

  return (
    <div className="relative animate-fade-in">
      {/* Ambient blobs behind the card */}
      <div className="absolute -top-8 -left-8 w-40 h-40 rounded-full
                      bg-accent/10 blur-3xl pointer-events-none" />
      <div className="absolute -bottom-8 -right-8 w-40 h-40 rounded-full
                      bg-accent-secondary/10 blur-3xl pointer-events-none" />

      {/* Progress ribbon */}
      <div className="mb-6">
        <div className="flex justify-between items-center text-xs text-text-muted mb-2">
          <span className="uppercase tracking-wider font-semibold text-accent">
            Question {index + 1} of {total}
          </span>
          {index > 0 && (
            <button
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
              className="hover:text-text-default transition-colors flex items-center gap-1"
            >
              ← Back
            </button>
          )}
        </div>
        <div className="relative h-2 w-full rounded-full bg-surface-border overflow-hidden">
          <div
            className="absolute inset-y-0 left-0 rounded-full
                       bg-gradient-to-r from-accent to-accent-secondary
                       transition-all duration-500 ease-out"
            style={{ width: `${((index + 1) / total) * 100}%` }}
          />
          {/* Shimmer sweep on the progress bar */}
          <div className="absolute inset-0 pointer-events-none
                          bg-gradient-to-r from-transparent via-white/30 to-transparent
                          -translate-x-full animate-shimmer" />
        </div>
      </div>

      {/* Question card */}
      <div
        key={current.id}
        className="relative rounded-2xl border border-surface-border
                   bg-surface-panel/80 backdrop-blur-md
                   shadow-2xl shadow-accent/5
                   overflow-hidden animate-question-in"
      >
        {/* Top gradient accent bar */}
        <div className="absolute inset-x-0 top-0 h-1
                        bg-gradient-to-r from-accent via-accent-secondary to-accent
                        bg-[length:200%_auto] animate-gradient" />

        <div className="p-8 md:p-10">
          {/* Question badge */}
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full
                          bg-accent/10 border border-accent/20 mb-5">
            <div className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            <span className="text-[10px] font-bold uppercase tracking-widest text-accent">
              Quick question
            </span>
          </div>

          {/* The question */}
          <h2 className="text-2xl md:text-3xl font-bold text-text-default mb-3 leading-tight">
            {current.reason}
          </h2>
          <p className="text-sm text-text-muted mb-8">
            Pick one of the options below, or type your own answer.
          </p>

          {/* Option pills */}
          <div className="flex flex-wrap gap-2.5 mb-8">
            {(() => {
              const allOpts = current.suggested_options ?? []
              const filtered = allOpts.filter((opt) => {
                if (isEngineeringLanguage(opt)) {
                  console.warn('[ClarificationWizard] Filtered engineering option:', opt)
                  return false
                }
                return true
              })
              return filtered.map((opt, i) => (
                <button
                  key={opt}
                  onClick={() => record(opt)}
                  className="group relative overflow-hidden
                             px-5 py-3 rounded-xl
                             bg-gradient-to-br from-accent/5 to-accent-secondary/5
                             border border-accent/20
                             text-text-default text-sm font-medium
                             hover:border-accent/50
                             hover:from-accent/10 hover:to-accent-secondary/10
                             hover:shadow-lg hover:shadow-accent/15
                             hover:-translate-y-0.5
                             active:translate-y-0
                             transition-all duration-200
                             animate-fade-in"
                  style={{ animationDelay: `${i * 60}ms` }}
                >
                  <span className="relative z-10">{opt}</span>
                  {/* Hover shine sweep */}
                  <span className="absolute inset-0 pointer-events-none
                                   bg-gradient-to-tr from-transparent via-white/10 to-transparent
                                   -translate-x-full group-hover:translate-x-full
                                   transition-transform duration-700" />
                </button>
              ))
            })()}
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3 mb-4">
            <div className="flex-1 border-t border-surface-border/60" />
            <span className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
              Or type your own
            </span>
            <div className="flex-1 border-t border-surface-border/60" />
          </div>

          {/* Free-text input */}
          <div className="flex gap-2">
            <input
              type="text"
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && freeText.trim()) record(freeText.trim()) }}
              placeholder="Type your answer…"
              className="flex-1 px-4 py-3 rounded-xl
                         bg-surface-page/50 border border-surface-border
                         text-text-default placeholder:text-text-muted/60
                         focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent
                         transition-all duration-200"
            />
            <button
              disabled={!freeText.trim()}
              onClick={() => { if (freeText.trim()) record(freeText.trim()) }}
              className="px-6 py-3 rounded-xl font-semibold text-sm text-white
                         bg-gradient-to-r from-accent to-accent-secondary
                         shadow-lg shadow-accent/25
                         hover:shadow-xl hover:shadow-accent/35
                         hover:scale-[1.02] active:scale-[0.98]
                         disabled:opacity-40 disabled:cursor-not-allowed
                         disabled:scale-100 disabled:shadow-none
                         transition-all duration-200"
            >
              {isLast ? 'Finish' : 'Next →'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
