import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, FolderOpen, Trash2, ExternalLink } from 'lucide-react'
import { toast } from 'sonner'

import { useAuth } from '../context/AuthContext'
import { listProjects, ensureUserProfile, deleteProject } from '../api/projects'
import { useActiveGeneration } from '../hooks/useActiveGeneration'

function routeForStatus(status, id) {
  const approval = new Set(['awaiting_approval', 'approved'])
  const generation = new Set([
    'blueprinting', 'generating', 'generated', 'testing', 'tested',
    'verifying', 'verified', 'deploying', 'packaging',
  ])
  const detail = new Set(['ready', 'ready_with_warnings', 'deployed', 'modifying', 'regenerating'])
  if (approval.has(status)) return `/projects/${id}/approve`
  if (generation.has(status)) return `/projects/${id}/generation`
  if (detail.has(status)) return `/projects/${id}/detail`
  return `/projects/${id}`
}

export default function Dashboard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [deletingId, setDeletingId] = useState(null)

  const displayName = user?.displayName || user?.email?.split('@')[0] || 'there'

  useEffect(() => {
    ensureUserProfile().catch((err) => {
      console.warn('ensureUserProfile failed', err.friendlyMessage)
    })
  }, [])

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: listProjects,
  })

  const { data: activeData } = useActiveGeneration()
  const activeProject = activeData?.project ?? null

  function onCreate() {
    if (activeProject) {
      toast.error(`Wait for "${activeProject.name}" to finish generating first.`)
      return
    }
    navigate('/projects/new')
  }

  async function handleDeleteProject(projectId) {
    setDeletingId(projectId)
    try {
      await deleteProject(projectId)
      toast.success('Project deleted')
      qc.invalidateQueries({ queryKey: ['projects'] })
      setDeleteConfirm(null)
    } catch (err) {
      toast.error('Failed to delete: ' + (err.message || 'Unknown error'))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="min-h-screen bg-surface-page">
      {/* Delete confirmation dialog */}
      {deleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => { if (!deletingId) setDeleteConfirm(null) }}
        >
          <div
            className="bg-surface-panel rounded-xl shadow-xl p-6 max-w-sm w-full mx-4 border border-surface-border"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-text-default mb-2">Delete project?</h2>
            <p className="text-sm text-text-muted mb-6">
              Are you sure you want to delete{' '}
              <span className="font-medium text-text-default">"{deleteConfirm.name}"</span>?
              This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                disabled={!!deletingId}
                className="btn-secondary disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDeleteProject(deleteConfirm.projectId)}
                disabled={!!deletingId}
                className="px-4 py-2 rounded-lg text-sm font-medium
                           bg-red-600 hover:bg-red-700 text-white
                           disabled:opacity-60 transition-colors
                           inline-flex items-center gap-2"
              >
                {deletingId === deleteConfirm.projectId ? (
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
          </div>
        </div>
      )}

      {/* Main */}
      <main className="max-w-5xl mx-auto px-4 py-10">
        {/* Hero / greeting card */}
        <div className="relative overflow-hidden rounded-2xl
                        bg-gradient-to-br from-accent/10 via-accent-secondary/5 to-transparent
                        border border-surface-border p-8 mb-8">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(99,102,241,0.12),transparent_60%)]" />
          <div className="relative flex items-center justify-between flex-wrap gap-4">
            <div>
              <h1 className="text-3xl font-bold text-text-default mb-1">
                Welcome back, {displayName}!
              </h1>
              <p className="text-text-muted">Ready to build something new today?</p>
            </div>
            <button
              onClick={onCreate}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl
                         bg-gradient-to-r from-accent to-accent-secondary
                         text-white font-semibold
                         hover:shadow-lg hover:shadow-accent/30
                         hover:scale-[1.02] active:scale-[0.98]
                         disabled:opacity-50 disabled:scale-100
                         transition-all duration-200"
              disabled={!!activeProject}
              title={activeProject ? `Wait for "${activeProject.name}" to finish generating` : 'Start a new project'}
            >
              <Plus className="w-4 h-4" />
              New project
            </button>
          </div>
        </div>

        {/* Project list section header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-text-default">Your projects</h2>
        </div>

        {isLoading ? (
          <p className="text-text-muted">Loading projects…</p>
        ) : !projects || projects.length === 0 ? (
          <EmptyState onCreate={onCreate} />
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <li
                key={p.id}
                className={`group p-5 rounded-xl bg-surface-panel border border-surface-border
                            hover:border-accent/50 hover:shadow-lg hover:shadow-accent/10
                            hover:-translate-y-0.5 transition-all duration-200 cursor-pointer
                            ${deletingId === p.id ? 'opacity-50 pointer-events-none' : ''}`}
                onClick={() => navigate(routeForStatus(p.status, p.id))}
              >
                <div className="flex items-start justify-between mb-3">
                  <FolderOpen className="w-5 h-5 text-accent" />
                  <div className="flex items-center gap-2">
                    <StatusPill status={p.status} />
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setDeleteConfirm({ projectId: p.id, name: p.name })
                      }}
                      className="text-text-muted hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30 rounded p-0.5 transition-colors"
                      title="Delete project"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <h3 className="font-medium text-text-default mb-1 truncate">{p.name}</h3>
                <p className="text-xs text-text-muted">id: {p.id}</p>
                {(p.status === 'ready' || p.status === 'ready_with_warnings' || p.status === 'modifying' || p.status === 'regenerating') && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      navigate(`/projects/${p.id}/detail`)
                    }}
                    className="mt-3 flex items-center gap-1 text-xs text-accent hover:text-accent-secondary font-medium transition-colors"
                  >
                    <ExternalLink size={12} />
                    View App
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

      </main>
    </div>
  )
}

function EmptyState({ onCreate }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center
                    rounded-2xl border border-surface-border bg-surface-panel">
      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent/20 to-accent-secondary/20
                      flex items-center justify-center mb-4">
        <FolderOpen className="w-8 h-8 text-accent" />
      </div>
      <h3 className="font-semibold text-text-default mb-1">No projects yet</h3>
      <p className="text-sm text-text-muted mb-6 max-w-xs">
        Start by creating a project, then describe what you want to build.
      </p>
      <button
        onClick={onCreate}
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl
                   bg-gradient-to-r from-accent to-accent-secondary
                   text-white font-semibold
                   hover:shadow-lg hover:shadow-accent/30 hover:scale-[1.02]
                   transition-all duration-200"
      >
        <Plus className="w-4 h-4" />
        Create your first project
      </button>
    </div>
  )
}

function StatusPill({ status }) {
  const styles = {
    awaiting_input:           'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    structuring:              'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    clarifying:               'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    awaiting_approval:        'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
    blueprinting:             'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    generating:               'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    verifying:                'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    packaging:                'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    deploying:                'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
    ready:                    'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    ready_with_warnings:      'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
    deployed:                 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    modifying:                'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
    regenerating:             'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
    deployment_failed:        'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
    failed:                   'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  }
  const cls = styles[status] || 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
      {status?.replace(/_/g, ' ')}
    </span>
  )
}
