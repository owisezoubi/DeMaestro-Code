import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Plus, FolderOpen, Trash2, ExternalLink,
  Sparkles, Clock, ArrowRight, Loader2
} from 'lucide-react'
import { toast } from 'sonner'

import { useAuth } from '../context/AuthContext'
import { listProjects, ensureUserProfile } from '../api/projects'
import { useActiveGeneration } from '../hooks/useActiveGeneration'
import { deleteProjectAndPurge } from '../lib/deleteWithCachePurge'
import ProjectCardSkeleton from '../components/ProjectCardSkeleton'

function routeForStatus(status, id) {
  const approval = new Set(['awaiting_approval'])
  const generation = new Set([
    'approved',                    // already approved → pipeline should be running
    'blueprinting', 'generating', 'generated', 'testing', 'tested',
    'verifying', 'verified', 'deploying', 'packaging',
    'ready', 'ready_with_warnings', 'deployed',
    'modifying', 'regenerating',
    'tests_failed_recoverable', 'failed', 'stopped',
  ])
  if (approval.has(status)) return `/projects/${id}/approve`
  if (generation.has(status)) return `/projects/${id}/generation`
  return `/projects/${id}`
}

export default function Dashboard() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [deletingIds, setDeletingIds] = useState(() => new Set())

  const displayName = user?.displayName || user?.email?.split('@')[0] || 'there'

  useEffect(() => {
    ensureUserProfile().catch((err) => {
      console.warn('ensureUserProfile failed', err.friendlyMessage)
    })
  }, [])

  const { data: projects, isLoading, isFetching } = useQuery({
    queryKey: ['projects'],
    queryFn: listProjects,
    refetchOnMount: 'always',
    staleTime: 0,
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
    setDeletingIds((prev) => { const next = new Set(prev); next.add(projectId); return next })
    setDeleteConfirm(null)
    try {
      await deleteProjectAndPurge(qc, projectId)
      toast.success('Project deleted')
    } catch (err) {
      toast.error('Failed to delete: ' + (err.message || 'Unknown error'))
    } finally {
      setDeletingIds((prev) => { const next = new Set(prev); next.delete(projectId); return next })
    }
  }

  return (
    <div className="min-h-screen bg-surface-page">
      {/* Delete confirmation dialog */}
      {deleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setDeleteConfirm(null)}
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
              <button onClick={() => setDeleteConfirm(null)} className="btn-secondary">
                Cancel
              </button>
              <button
                onClick={() => handleDeleteProject(deleteConfirm.projectId)}
                disabled={deletingIds.has(deleteConfirm.projectId)}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                           bg-red-600 hover:bg-red-700 disabled:opacity-60 disabled:cursor-not-allowed
                           text-white transition-colors"
              >
                {deletingIds.has(deleteConfirm.projectId) ? (
                  <>
                    <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent
                                     rounded-full animate-spin" />
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

      <main className="max-w-5xl mx-auto px-4 py-10">
        {/* Greeting hero card */}
        <div className="relative overflow-hidden rounded-3xl
                        bg-gradient-to-br from-accent/15 via-accent-secondary/10 to-transparent
                        border border-surface-border p-8 md:p-10 mb-10">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(124,58,237,0.15),transparent_60%)]" />
          <div className="absolute -top-16 -right-16 w-64 h-64 rounded-full
                          bg-accent/15 blur-3xl pointer-events-none" />
          <div className="absolute -bottom-16 -left-16 w-56 h-56 rounded-full
                          bg-accent-secondary/[0.12] blur-3xl pointer-events-none" />

          <div className="relative flex items-center justify-between flex-wrap gap-4">
            <div>
              <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full
                              bg-accent/10 border border-accent/20 mb-3">
                <Sparkles className="w-3 h-3 text-accent" />
                <span className="text-[10px] font-bold uppercase tracking-widest text-accent">
                  Your workspace
                </span>
              </div>
              <h1 className="text-4xl md:text-5xl font-black text-text-default mb-2 leading-tight">
                Welcome back,{' '}
                <span className="bg-gradient-to-r from-accent to-accent-secondary
                                 bg-clip-text text-transparent">
                  {displayName}
                </span>
              </h1>
              <p className="text-text-muted text-base">Ready to build something new today?</p>
            </div>

            <button
              onClick={onCreate}
              className="group relative inline-flex items-center gap-2
                         px-6 py-3.5 rounded-2xl overflow-hidden
                         bg-gradient-to-r from-accent to-accent-secondary
                         text-white font-bold text-sm
                         shadow-xl shadow-accent/30
                         hover:shadow-2xl hover:shadow-accent/45
                         hover:scale-[1.03] active:scale-[0.98]
                         disabled:opacity-50 disabled:scale-100
                         transition-all duration-200"
              disabled={!!activeProject}
              title={activeProject ? `Wait for "${activeProject.name}" to finish generating` : 'Start a new project'}
            >
              <span className="absolute inset-0 pointer-events-none
                               bg-gradient-to-r from-transparent via-white/25 to-transparent
                               -translate-x-full group-hover:translate-x-full
                               transition-transform duration-700" />
              <Plus className="w-4 h-4 relative z-10" />
              <span className="relative z-10">New project</span>
            </button>
          </div>
        </div>

        {/* Section header */}
        <div className="flex items-center gap-3 mb-6">
          <h2 className="text-2xl font-bold text-text-default">Your projects</h2>
          {projects && projects.length > 0 && (
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full
                             bg-accent/10 text-accent text-xs font-bold">
              {projects.length}
            </span>
          )}
          {deletingIds.size > 0 && (
            <span className="inline-flex items-center gap-1.5 text-xs text-red-500 animate-fade-in">
              <Trash2 className="w-3 h-3" />
              Deleting {deletingIds.size}…
            </span>
          )}
          {isFetching && !isLoading && (
            <span className="inline-flex items-center gap-1.5 text-xs text-text-muted animate-fade-in">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              Syncing
            </span>
          )}
        </div>

        {isLoading ? (
          <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <ProjectCardSkeleton key={i} delay={i * 80} />
            ))}
          </ul>
        ) : !projects || projects.length === 0 ? (
          <EmptyState onCreate={onCreate} />
        ) : (
          <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 animate-fade-in">
            {projects.map((p, idx) => {
              const isDone = ['ready', 'ready_with_warnings', 'deployed'].includes(p.status)
              const isFailed = p.status === 'failed'
              const isActive = ['blueprinting', 'generating', 'testing', 'debugging',
                                'verifying', 'packaging', 'deploying', 'clarifying',
                                'structuring'].includes(p.status)

              return (
                <li
                  key={p.id}
                  className={`relative group rounded-2xl overflow-hidden
                              bg-surface-panel border transition-all duration-300
                              animate-card-enter
                              ${deletingIds.has(p.id)
                                ? 'pointer-events-none border-red-500/40'
                                : 'border-surface-border hover:border-accent/40 hover:shadow-2xl hover:shadow-accent/15 hover:-translate-y-1 cursor-pointer'}`}
                  style={{ animationDelay: `${Math.min(idx, 8) * 60}ms` }}
                  onClick={() => !deletingIds.has(p.id) && navigate(routeForStatus(p.status, p.id))}
                >
                  {/* Deletion overlay */}
                  {deletingIds.has(p.id) && (
                    <div className="absolute inset-0 z-20 flex items-center justify-center overflow-hidden">
                      <div
                        className="absolute inset-0 animate-spin"
                        style={{
                          animationDuration: '1.8s',
                          background:
                            'conic-gradient(from 0deg, transparent 0%, rgba(239,68,68,0.55) 20%, transparent 40%)',
                        }}
                      />
                      <div className="absolute inset-[2px] rounded-[14px] bg-surface-panel/70 backdrop-blur-sm" />
                      <div className="relative z-10 flex flex-col items-center gap-1.5">
                        <Trash2 className="w-7 h-7 text-red-400 animate-pulse" />
                        <span className="text-sm font-semibold text-red-300">Deleting…</span>
                        <span className="text-[10px] text-red-400/60">Removing project files</span>
                      </div>
                      <div className="absolute inset-0 bg-gradient-to-r from-transparent
                                      via-red-400/10 to-transparent
                                      -translate-x-full animate-shimmer-sweep pointer-events-none" />
                    </div>
                  )}

                  {/* Hero band */}
                  <div className={`relative h-24 overflow-hidden
                                   ${deletingIds.has(p.id) ? 'opacity-20' : ''}
                                   ${isDone
                                     ? 'bg-gradient-to-br from-emerald-500/25 via-teal-500/15 to-accent/10'
                                     : isFailed
                                     ? 'bg-gradient-to-br from-red-500/25 via-rose-500/15 to-orange-500/10'
                                     : isActive
                                     ? 'bg-gradient-to-br from-accent/25 via-accent-secondary/20 to-accent/10'
                                     : 'bg-gradient-to-br from-accent/15 via-accent-secondary/10 to-accent/5'}`}>

                    <div className="absolute -right-6 -top-6 w-24 h-24 rounded-full
                                    bg-white/15 blur-2xl pointer-events-none" />
                    <div className="absolute -left-4 -bottom-4 w-20 h-20 rounded-full
                                    bg-white/[0.08] blur-2xl pointer-events-none" />

                    {/* Folder icon */}
                    <div className="absolute top-4 left-5">
                      <div className={`w-11 h-11 rounded-xl backdrop-blur-sm
                                       flex items-center justify-center
                                       transition-transform duration-500
                                       ${isDone
                                         ? 'bg-emerald-500/25 group-hover:scale-110 group-hover:rotate-3'
                                         : isFailed
                                         ? 'bg-red-500/25 group-hover:scale-110'
                                         : 'bg-accent/25 group-hover:scale-110 group-hover:rotate-3'}`}>
                        <FolderOpen className={`w-5 h-5
                                               ${isDone ? 'text-emerald-500'
                                                 : isFailed ? 'text-red-500'
                                                 : 'text-accent'}`} />
                      </div>
                    </div>

                    {/* Status pill */}
                    <div className="absolute top-4 right-4">
                      <StatusPill status={p.status} />
                    </div>

                    {/* Active sliding pulse bar */}
                    {isActive && (
                      <div className="absolute bottom-0 left-0 right-0 h-0.5 overflow-hidden">
                        <div className="h-full bg-gradient-to-r from-transparent via-accent to-transparent
                                        animate-slide-pulse" />
                      </div>
                    )}
                  </div>

                  {/* Content */}
                  <div className={`p-6 ${deletingIds.has(p.id) ? 'opacity-20 blur-[1px]' : ''}`}>
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <h3 className="font-bold text-lg text-text-default leading-tight
                                     line-clamp-2 group-hover:text-accent
                                     transition-colors duration-200">
                        {p.name}
                      </h3>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteConfirm({ projectId: p.id, name: p.name })
                        }}
                        className="flex-shrink-0 w-7 h-7 rounded-lg
                                   text-text-muted hover:text-red-500 hover:bg-red-500/10
                                   opacity-0 group-hover:opacity-100
                                   flex items-center justify-center
                                   transition-all duration-200"
                        title="Delete project"
                        aria-label="Delete project"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>

                    <div className="flex items-center gap-2 text-xs text-text-muted mb-4">
                      <Clock className="w-3 h-3" />
                      <span className="font-mono truncate">{p.id.slice(0, 12)}</span>
                    </div>

                    <div className="flex items-center justify-between pt-3
                                    border-t border-surface-border/60">
                      <span className="text-xs font-semibold uppercase tracking-widest text-text-muted">
                        {isDone ? 'View app' : isFailed ? 'View error' : isActive ? 'In progress' : 'Continue'}
                      </span>
                      {isActive ? (
                        <Loader2 className="w-4 h-4 text-accent animate-spin" />
                      ) : (
                        <ArrowRight className="w-4 h-4 text-accent
                                               group-hover:translate-x-1
                                               transition-transform duration-200" />
                      )}
                    </div>
                  </div>

                  {/* Hover shine */}
                  <div className="absolute inset-0 pointer-events-none
                                  bg-gradient-to-tr from-transparent via-white/5 to-transparent
                                  opacity-0 group-hover:opacity-100
                                  transition-opacity duration-500" />
                </li>
              )
            })}
          </ul>
        )}
      </main>
    </div>
  )
}

function EmptyState({ onCreate }) {
  return (
    <div className="relative overflow-hidden py-20 text-center
                    rounded-3xl border border-surface-border
                    bg-surface-panel">
      <div className="absolute -top-20 -left-20 w-64 h-64 rounded-full
                      bg-accent/10 blur-3xl pointer-events-none" />
      <div className="absolute -bottom-20 -right-20 w-64 h-64 rounded-full
                      bg-accent-secondary/10 blur-3xl pointer-events-none" />

      <div className="relative">
        <div className="w-20 h-20 mx-auto mb-6 rounded-3xl
                        bg-gradient-to-br from-accent to-accent-secondary
                        flex items-center justify-center
                        shadow-2xl shadow-accent/30
                        animate-float">
          <Sparkles className="w-9 h-9 text-white" />
        </div>

        <h3 className="text-xl font-bold text-text-default mb-2">
          Your first project starts here
        </h3>
        <p className="text-sm text-text-muted mb-8 max-w-sm mx-auto">
          Describe what you want to build in plain English —
          DeMaestro handles the rest, from code to live URL.
        </p>

        <button
          onClick={onCreate}
          className="group inline-flex items-center gap-2 px-7 py-3.5 rounded-xl
                     bg-gradient-to-r from-accent to-accent-secondary
                     text-white font-bold text-sm
                     shadow-xl shadow-accent/30
                     hover:shadow-2xl hover:shadow-accent/40
                     hover:scale-[1.03] active:scale-[0.98]
                     transition-all duration-200"
        >
          <Plus className="w-4 h-4" />
          Create your first project
          <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
        </button>
      </div>
    </div>
  )
}

function StatusPill({ status }) {
  const styles = {
    awaiting_input:      'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    structuring:         'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    clarifying:          'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    awaiting_approval:   'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
    blueprinting:        'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    generating:          'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    verifying:           'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    packaging:           'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    deploying:           'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
    ready:               'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    ready_with_warnings: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
    deployed:            'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    modifying:           'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
    regenerating:        'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
    deployment_failed:   'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
    failed:              'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  }
  const cls = styles[status] || 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
      {status?.replace(/_/g, ' ')}
    </span>
  )
}
