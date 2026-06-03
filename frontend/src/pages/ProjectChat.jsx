import { useState, useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ArrowLeft, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'

import Logo from '../components/Logo'
import ThemeToggle from '../components/ThemeToggle'
import { getProjectStatus } from '../api/requirements'
import { triggerStructuring, getClarifications, answerClarificationsBatch, getClarificationProgress, saveClarificationProgress } from '../api/structure'
import { getProject } from '../api/projects'
import { startGeneration } from '../api/generation'

const TERMINAL = new Set(['ready', 'ready_with_warnings', 'failed', 'awaiting_approval'])

const STATUS_STYLES = {
  awaiting_input: 'bg-slate-100 text-slate-700',
  structuring: 'bg-amber-100 text-amber-800',
  clarifying: 'bg-amber-100 text-amber-800',
  awaiting_approval: 'bg-orange-100 text-orange-800',
  blueprinting: 'bg-blue-100 text-blue-800',
  generating: 'bg-blue-100 text-blue-800',
  verifying: 'bg-blue-100 text-blue-800',
  packaging: 'bg-blue-100 text-blue-800',
  ready: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
}

const STATUS_DESC = {
  blueprinting: 'Designing the application blueprint…',
  generating: 'Generating your application code…',
  verifying: 'Verifying the generated code…',
  packaging: 'Packaging your application…',
  ready: 'Your application is ready!',
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

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900">
      <header className="border-b border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <div className="flex items-center gap-3">
            <ThemeToggle />
            <Link to="/dashboard" className="btn-secondary text-sm">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to dashboard
            </Link>
          </div>
        </div>
      </header>

      {status && (
        <div className="max-w-2xl mx-auto px-4 pt-4">
          <span
            className={`text-xs px-2.5 py-1 rounded-full font-medium ${STATUS_STYLES[status] ?? 'bg-slate-100 text-slate-700'}`}
          >
            {status.replace(/_/g, ' ')}
          </span>
        </div>
      )}

      <main className="max-w-2xl mx-auto px-4 py-6">
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
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <Loader2 className="w-8 h-8 text-primary-600 animate-spin mb-4" />
        <p className="font-medium text-slate-900 dark:text-slate-100">Analyzing your requirements with AI…</p>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Usually 10–30 seconds.</p>
      </div>
    )
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
      toast.success(
        isGenFailure
          ? 'Retrying code generation — your requirements are saved'
          : 'Re-analyzing your requirements…',
      )
      if (isGenFailure) {
        navigate(`/projects/${projectId}/generation`)
      }
    },
    onError: (err) =>
      toast.error(err.friendlyMessage || 'Retry failed. Please try again.'),
  })

  const title = isGenFailure ? 'Code generation failed' : 'Something went wrong'
  const description = isGenFailure
    ? "The previous attempt didn't finish. Your approved requirements and blueprint are saved — Retry will pick up from code generation."
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
      {errorMessage && (
        <div className="text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 rounded-md mb-4 max-w-md text-left font-mono break-words">
          <span className="font-semibold not-italic">Previous error:</span><br/>
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
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <Loader2 className="w-8 h-8 text-primary-600 animate-spin mb-4" />
        <p className="font-medium text-slate-900 dark:text-slate-100">Refining your requirements…</p>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          You answered everything — this part takes a moment.
        </p>
      </div>
    )
  }
  if (!clarifications.length) {
    return (
      <div className="card flex items-center gap-3 text-slate-500 dark:text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin" /><span>Loading questions…</span>
      </div>
    )
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
    <div className="card space-y-4">
      {/* progress */}
      <div>
        <div className="flex justify-between text-xs text-slate-500 dark:text-slate-400 mb-1">
          <span>Question {index + 1} of {total}</span>
          {index > 0 && (
            <button onClick={() => setIndex((i) => Math.max(0, i - 1))}
              className="hover:text-slate-800 dark:hover:text-slate-200">← Back</button>
          )}
        </div>
        <div className="h-1.5 w-full rounded-full bg-slate-100 dark:bg-slate-700 overflow-hidden">
          <div className="h-full bg-primary-600 transition-all"
               style={{ width: `${((index) / total) * 100}%` }} />
        </div>
      </div>

      <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">{current.reason}</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400">Pick the best option, or type your own.</p>

      <div className="flex flex-wrap gap-2">
        {(current.suggested_options ?? []).map((opt) => (
          <button key={opt} onClick={() => record(opt)}
            className="rounded-full bg-primary-50 dark:bg-primary-600/20 text-primary-700 dark:text-primary-200 px-4 py-2 text-sm hover:bg-primary-100 dark:hover:bg-primary-600/30 transition-colors">
            {opt}
          </button>
        ))}
        {(current.suggested_options ?? []).length > 0 && (
          <button
            onClick={() => record(current.suggested_options[0])}
            className="rounded-full bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-4 py-2 text-sm hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors"
            title={`Use ${current.suggested_options[0]}`}
          >
            I don&apos;t know — use the default:{' '}
            <span className="font-medium">{current.suggested_options[0]}</span>
          </button>
        )}
      </div>

      <div className="flex items-center gap-3">
        <div className="flex-1 border-t border-slate-200 dark:border-slate-700" />
        <span className="text-xs text-slate-400">Or type your own:</span>
        <div className="flex-1 border-t border-slate-200 dark:border-slate-700" />
      </div>

      <div className="flex gap-2">
        <input type="text" value={freeText} onChange={(e) => setFreeText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && freeText.trim()) record(freeText.trim()) }}
          placeholder="Type your answer…" className="input flex-1" />
        <button disabled={!freeText.trim()} onClick={() => { if (freeText.trim()) record(freeText.trim()) }}
          className="btn-primary">
          {isLast ? 'Finish' : 'Next'}
        </button>
      </div>
    </div>
  )
}
