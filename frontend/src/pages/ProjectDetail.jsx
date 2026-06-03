import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Download, Pencil, Loader2, History, CheckCircle2 } from 'lucide-react'
import { toast } from 'sonner'

import Logo from '../components/Logo'
import ThemeToggle from '../components/ThemeToggle'
import { getProject, requestChanges } from '../api/projects'

const IN_PROGRESS_STATUSES = new Set([
  'modifying', 'testing', 'tested', 'debugging', 'verifying', 'packaging', 'regenerating',
])

export default function ProjectDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', id],
    queryFn: () => getProject(id),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return IN_PROGRESS_STATUSES.has(s) ? 2000 : false
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-900">
        Loading…
      </div>
    )
  }

  if (!project) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-900">
        Project not found.
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900">
      <header className="border-b border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <div className="flex items-center gap-3">
            <ThemeToggle />
            <button onClick={() => navigate('/dashboard')} className="btn-secondary">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Dashboard
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-10">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">{project.name}</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
              Status: <span className="font-medium">{project.status?.replace(/_/g, ' ')}</span>
              {IN_PROGRESS_STATUSES.has(project.status) && project.current_stage && project.current_stage !== project.status && (
                <span className="ml-2 text-slate-400 dark:text-slate-500">
                  · stage: {project.current_stage.replace(/_/g, ' ')}
                </span>
              )}
            </p>
          </div>

          {project.zip_url && !IN_PROGRESS_STATUSES.has(project.status) && (
            <a href={project.zip_url} download className="btn-primary">
              <Download className="w-4 h-4 mr-2" />
              Download ZIP
            </a>
          )}
        </div>

        {project.status === 'deployment_failed' && (
          <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm mb-8">
            Regeneration failed.{project.error_message ? ` ${project.error_message}` : ''}
          </div>
        )}

        {(project.status === 'ready' || project.status === 'ready_with_warnings') && (
          <>
            <ChangeRequestPanel projectId={id} project={project} qc={qc} />
            <ModificationHistoryPanel history={project?.modification_history} />
          </>
        )}

        {IN_PROGRESS_STATUSES.has(project.status) && project.status !== 'ready' && (
          <>
            <ChangeRequestPanel projectId={id} project={project} qc={qc} />
            <ModificationHistoryPanel history={project?.modification_history} />
          </>
        )}

        {project.modification_round != null && project.modification_round > 0 && (
          <p className="text-xs text-slate-400 dark:text-slate-500 mt-8">
            Modification round: {project.modification_round_completed ?? 0} / {project.modification_round}
          </p>
        )}
      </main>
    </div>
  )
}

function ChangeRequestPanel({ projectId, project, qc }) {
  const [text, setText] = useState('')
  const inProgress = IN_PROGRESS_STATUSES.has(project?.status)
  const lastSummary = project?.last_modification_summary
  const lastFiles = project?.modified_files_last || []

  const mut = useMutation({
    mutationFn: () => requestChanges(projectId, text.trim()),
    onSuccess: () => {
      toast.success('Working on your changes…')
      setText('')
      qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
    onError: (err) =>
      toast.error(err.friendlyMessage || err.message || 'Could not start change'),
  })

  return (
    <div className="card mt-6">
      <div className="flex items-center gap-2 mb-3">
        <Pencil className="w-5 h-5 text-primary-600" />
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Request changes
        </h2>
      </div>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
        Describe what you want changed (e.g. "make the navbar dark blue",
        "add an 'About' page", "show the price in EUR"). DeMaestro will edit only
        the files needed, re-test, and give you a new download link.
      </p>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        className="input w-full mb-3 resize-y"
        placeholder="What should change?"
        disabled={inProgress || mut.isPending}
      />

      {inProgress && (
        <div className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-400 mb-3">
          <Loader2 className="w-4 h-4 animate-spin" />
          {project?.current_stage
            ? `Stage: ${project.current_stage.replace(/_/g, ' ')}…`
            : 'Working on your changes…'}
        </div>
      )}

      {!inProgress && lastSummary && (
        <div className="border-l-4 border-emerald-500 bg-emerald-50/40 dark:bg-emerald-600/10 p-3 rounded-r-md mb-3">
          <div className="flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
                Last change: {lastSummary}
              </p>
              {lastFiles.length > 0 && (
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                  {lastFiles.length} file{lastFiles.length === 1 ? '' : 's'} updated:{' '}
                  {lastFiles.slice(0, 3).join(', ')}
                  {lastFiles.length > 3 && ` +${lastFiles.length - 3} more`}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="flex justify-end gap-2">
        {project?.zip_url && !inProgress && (
          <a href={project.zip_url} className="btn-secondary">
            <Download className="w-4 h-4 mr-2" /> Download latest
          </a>
        )}
        <button
          className="btn-primary"
          onClick={() => mut.mutate()}
          disabled={!text.trim() || inProgress || mut.isPending}
        >
          {mut.isPending ? 'Submitting…' : 'Apply change'}
        </button>
      </div>
    </div>
  )
}

function ModificationHistoryPanel({ history }) {
  if (!history || history.length === 0) return null
  const sorted = [...history].reverse().slice(0, 10)
  return (
    <div className="card mt-6">
      <div className="flex items-center gap-2 mb-3">
        <History className="w-5 h-5 text-slate-500" />
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
          Change history
        </h2>
      </div>
      <ol className="space-y-3">
        {sorted.map((h, i) => (
          <li key={i} className="border-l-2 border-slate-200 dark:border-slate-700 pl-3">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
              {h.summary || h.request}
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {new Date(h.timestamp).toLocaleString()}
              {h.files_changed?.length
                ? ` · ${h.files_changed.length} file(s)`
                : ''}
            </p>
          </li>
        ))}
      </ol>
    </div>
  )
}
