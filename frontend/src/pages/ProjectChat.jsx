import { useState, useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ArrowLeft, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'

import Logo from '../components/Logo'
import { getProjectStatus } from '../api/requirements'
import { triggerStructuring, getClarifications, answerClarification } from '../api/structure'

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
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <Link to="/dashboard" className="btn-secondary text-sm">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back to dashboard
          </Link>
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
      <div className="card flex items-center gap-3 text-slate-500">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Loading…</span>
      </div>
    )
  }

  if (status === 'awaiting_input') {
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <p className="text-slate-500 mb-4">No requirements submitted yet.</p>
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
        <p className="font-medium text-slate-900">Analyzing your requirements with AI…</p>
        <p className="text-sm text-slate-500 mt-1">Usually 10–30 seconds.</p>
      </div>
    )
  }

  if (status === 'clarifying') {
    return (
      <div className="space-y-4">
        {clarificationsLoading && (
          <div className="p-4 bg-blue-50 rounded">
            <p>Processing your answer...</p>
            <p className="text-sm text-gray-600">This may take 30-60 seconds</p>
          </div>
        )}
        <ClarificationCard
          key={clarifications[0]?.id ?? 'none'}
          clarifications={clarifications}
          projectId={projectId}
          qc={qc}
        />
      </div>
    )
  }

  if (status === 'awaiting_approval') {
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <CheckCircle2 className="w-10 h-10 text-emerald-500 mb-4" />
        <h2 className="text-lg font-semibold text-slate-900 mb-1">
          Your requirements are ready!
        </h2>
        <p className="text-sm text-slate-500 mb-6">
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
      <p className="font-medium text-slate-900">Status: {status.replace(/_/g, ' ')}</p>
      <p className="text-sm text-slate-500 mt-1">
        {STATUS_DESC[status] ?? 'Processing…'}
      </p>
    </div>
  )
}

function FailedCard({ projectId, qc }) {
  const retryMut = useMutation({
    mutationFn: () => triggerStructuring(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['project-status', projectId] }),
    onError: (err) =>
      toast.error(err.friendlyMessage || 'Failed to retry. Please try again.'),
  })

  return (
    <div className="card flex flex-col items-center py-12 text-center">
      <AlertCircle className="w-10 h-10 text-red-500 mb-4" />
      <h2 className="text-lg font-semibold text-slate-900 mb-1">Something went wrong</h2>
      <p className="text-sm text-slate-500 mb-6">
        We couldn&apos;t analyze your requirements.
      </p>
      <button
        className="btn-secondary"
        onClick={() => retryMut.mutate()}
        disabled={retryMut.isPending}
      >
        {retryMut.isPending ? 'Retrying…' : 'Retry'}
      </button>
    </div>
  )
}

function ClarificationCard({ clarifications, projectId, qc }) {
  const [freeText, setFreeText] = useState('')

  const answerMut = useMutation({
    mutationFn: ({ ambiguityId, answer }) =>
      answerClarification(projectId, ambiguityId, answer),
    onSuccess: async () => {
      await Promise.all([
        qc.refetchQueries({ queryKey: ['clarifications', projectId], type: 'all' }),
        qc.refetchQueries({ queryKey: ['project-status', projectId], type: 'all' }),
      ])
      setTimeout(() => {
        qc.refetchQueries({ queryKey: ['clarifications', projectId], type: 'all' })
      }, 300)
    },
    onSettled: () => {
      setFreeText('')
    },
    onError: (err) => {
      const msg = err.friendlyMessage || err.message || ''
      const isTimeout =
        err.response?.status === 504 ||
        err.code === 'ECONNABORTED' ||
        msg.toLowerCase().includes('timeout')
      if (isTimeout) {
        toast.info('Still processing — this can take 1–2 minutes')
      } else {
        toast.error(msg || 'Failed to submit answer. Please try again.')
      }
    },
  })

  function submit(answer) {
    answerMut.mutate({ ambiguityId: clarifications[0].id, answer })
  }

  if (!clarifications.length) {
    return (
      <div className="card flex items-center gap-3 text-slate-500">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span>Loading questions…</span>
      </div>
    )
  }

  if (answerMut.isPending) {
    return (
      <div className="card flex flex-col items-center py-12 text-center">
        <Loader2 className="w-8 h-8 text-primary-600 animate-spin mb-4" />
        <p className="text-slate-600">Refining your requirements…</p>
      </div>
    )
  }

  const current = clarifications[0]
  const N = clarifications.length

  return (
    <div className="card space-y-4">
      <p className="text-sm text-slate-500">
        {N} question{N === 1 ? '' : 's'} remaining
      </p>

      <h2 className="text-base font-semibold text-slate-900">{current.reason}</h2>

      <p className="text-sm text-slate-500">
        Pick the best option below, or type your own answer.
      </p>

      <div className="flex flex-wrap gap-2">
        {(current.suggested_options ?? []).map((opt) => (
          <button
            key={opt}
            onClick={() => submit(opt)}
            className="rounded-full bg-primary-50 text-primary-700 px-4 py-2 text-sm hover:bg-primary-100 transition-colors cursor-pointer"
          >
            {opt}
          </button>
        ))}
        <button
          onClick={() => submit(current.suggested_options?.[0] ?? 'default')}
          className="rounded-full bg-slate-100 text-slate-600 px-4 py-2 text-sm hover:bg-slate-200 transition-colors cursor-pointer"
        >
          I don&apos;t know — pick a default
        </button>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex-1 border-t border-slate-200" />
        <span className="text-xs text-slate-400">Or type your own answer:</span>
        <div className="flex-1 border-t border-slate-200" />
      </div>

      <div className="flex gap-2">
        <input
          type="text"
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && freeText.trim()) submit(freeText.trim())
          }}
          placeholder="Type your answer…"
          className="input flex-1"
        />
        <button
          disabled={!freeText.trim()}
          onClick={() => {
            if (freeText.trim()) submit(freeText.trim())
          }}
          className="btn-primary"
        >
          Send
        </button>
      </div>
    </div>
  )
}
