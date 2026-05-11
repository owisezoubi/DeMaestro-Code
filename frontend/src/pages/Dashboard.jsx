import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { LogOut, Plus, FolderOpen } from 'lucide-react'

import Logo from '../components/Logo'
import { useAuth } from '../context/AuthContext'
import { listProjects, ensureUserProfile } from '../api/projects'

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

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
                  <StatusPill status={p.status} />
                </div>
                <h3 className="font-medium text-slate-900 mb-1 truncate">
                  {p.name}
                </h3>
                <p className="text-xs text-slate-500">id: {p.id}</p>
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
    failed: 'bg-red-100 text-red-800',
  }
  const cls = styles[status] || 'bg-slate-100 text-slate-700'
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${cls}`}>
      {status?.replace(/_/g, ' ')}
    </span>
  )
}
