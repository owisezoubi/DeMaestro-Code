import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { LogOut, Plus, FolderOpen, Trash2, ExternalLink } from 'lucide-react'
import { toast } from 'sonner'

import Logo from '../components/Logo'
import { useAuth } from '../context/AuthContext'
import { listProjects, ensureUserProfile, deleteProject } from '../api/projects'

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [deleteConfirm, setDeleteConfirm] = useState(null) // { projectId, name }

  // Make sure the backend has a user doc for this UID.
  useEffect(() => {
    ensureUserProfile().catch((err) => {
      console.warn('ensureUserProfile failed', err.friendlyMessage)
    })
  }, [])

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: listProjects,
  })

  function onCreate() {
    navigate('/projects/new')
  }

  async function onLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  async function handleDeleteProject(projectId) {
    try {
      await deleteProject(projectId)
      toast.success('Project deleted')
      qc.invalidateQueries({ queryKey: ['projects'] })
      setDeleteConfirm(null)
    } catch (err) {
      toast.error('Failed to delete: ' + (err.message || 'Unknown error'))
    }
  }

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <div className="flex items-center gap-3">
            <span className="text-sm text-slate-600 hidden sm:inline">
              {user?.email}
            </span>
            <button
              onClick={onLogout}
              className="btn-secondary"
              title="Sign out"
            >
              <LogOut className="w-4 h-4 mr-2" />
              Sign out
            </button>
          </div>
        </div>
      </header>

      {/* Delete confirmation dialog */}
      {deleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setDeleteConfirm(null)}
        >
          <div
            className="bg-white rounded-xl shadow-xl p-6 max-w-sm w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-slate-900 mb-2">Delete project?</h2>
            <p className="text-sm text-slate-600 mb-6">
              Are you sure you want to delete <span className="font-medium">"{deleteConfirm.name}"</span>?
              This cannot be undone. All requirements, notes, and generated files will be permanently deleted.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={() => handleDeleteProject(deleteConfirm.projectId)}
                className="px-4 py-2 rounded-lg text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors"
              >
                Delete project
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main */}
      <main className="max-w-5xl mx-auto px-4 py-10">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-semibold text-slate-900">
            Your projects
          </h1>
          <button
            onClick={onCreate}
            className="btn-primary"
          >
            <Plus className="w-4 h-4 mr-2" />
            New project
          </button>
        </div>

        {isLoading ? (
          <p className="text-slate-500">Loading projects…</p>
        ) : !projects || projects.length === 0 ? (
          <EmptyState onCreate={onCreate} />
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <li
                key={p.id}
                className="card hover:shadow-md transition-shadow cursor-pointer"
                onClick={() => navigate(`/projects/${p.id}`)}
              >
                <div className="flex items-start justify-between mb-2">
                  <FolderOpen className="w-5 h-5 text-primary-600" />
                  <div className="flex items-center gap-2">
                    <StatusPill status={p.status} />
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        setDeleteConfirm({ projectId: p.id, name: p.name })
                      }}
                      className="text-slate-400 hover:text-red-600 hover:bg-red-50 rounded p-0.5 transition-colors"
                      title="Delete project"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <h3 className="font-medium text-slate-900 mb-1 truncate">
                  {p.name}
                </h3>
                <p className="text-xs text-slate-500">id: {p.id}</p>
                {(p.status === 'deployed' || p.status === 'modifying' || p.status === 'regenerating' || p.status === 'deployment_failed') && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      navigate(`/projects/${p.id}/detail`)
                    }}
                    className="mt-2 flex items-center gap-1 text-xs text-primary-600 hover:text-primary-800 font-medium"
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
    <div className="card flex flex-col items-center justify-center py-16 text-center">
      <FolderOpen className="w-10 h-10 text-primary-400 mb-3" />
      <h3 className="font-medium text-slate-900 mb-1">No projects yet</h3>
      <p className="text-sm text-slate-500 mb-4">
        Start by creating a project, then describe what you want to build.
      </p>
      <button onClick={onCreate} className="btn-primary">
        <Plus className="w-4 h-4 mr-2" />
        Create your first project
      </button>
    </div>
  )
}

function StatusPill({ status }) {
  const styles = {
    awaiting_input: 'bg-slate-100 text-slate-700',
    structuring: 'bg-amber-100 text-amber-800',
    clarifying: 'bg-amber-100 text-amber-800',
    awaiting_approval: 'bg-orange-100 text-orange-800',
    blueprinting: 'bg-blue-100 text-blue-800',
    generating: 'bg-blue-100 text-blue-800',
    verifying: 'bg-blue-100 text-blue-800',
    packaging: 'bg-blue-100 text-blue-800',
    ready: 'bg-emerald-100 text-emerald-800',
    deployed: 'bg-emerald-100 text-emerald-800',
    modifying: 'bg-violet-100 text-violet-800',
    regenerating: 'bg-violet-100 text-violet-800',
    deployment_failed: 'bg-red-100 text-red-800',
    failed: 'bg-red-100 text-red-800',
  }
  const cls = styles[status] || 'bg-slate-100 text-slate-700'
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${cls}`}>
      {status?.replace(/_/g, ' ')}
    </span>
  )
}
