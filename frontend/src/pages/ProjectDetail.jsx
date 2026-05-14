import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { toast } from 'sonner'

import Logo from '../components/Logo'
import { getProject, requestChanges } from '../api/projects'

export default function ProjectDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [changeRequest, setChangeRequest] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', id],
    queryFn: () => getProject(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'modifying' || status === 'regenerating' ? 3000 : false
    },
  })

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500">
        Loading…
      </div>
    )
  }

  if (!project) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500">
        Project not found.
      </div>
    )
  }

  async function handleRequestChanges() {
    if (!changeRequest.trim()) {
      toast.error('Please describe the changes you want')
      return
    }
    setIsSubmitting(true)
    try {
      await requestChanges(id, changeRequest)
      toast.success('Changes requested. Regenerating…')
      setChangeRequest('')
      queryClient.invalidateQueries({ queryKey: ['project', id] })
    } catch (err) {
      toast.error('Failed: ' + (err.friendlyMessage || err.message))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <button
            onClick={() => navigate('/dashboard')}
            className="btn-secondary"
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Dashboard
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-10">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">{project.name}</h1>
            <p className="text-sm text-slate-500 mt-1">
              Status: <span className="font-medium">{project.status?.replace(/_/g, ' ')}</span>
            </p>
          </div>

          {project.deployment_url && (
            <a
              href={project.deployment_url}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-primary"
            >
              <ExternalLink className="w-4 h-4 mr-2" />
              Visit App
            </a>
          )}
        </div>

        {(project.status === 'modifying' || project.status === 'regenerating') && (
          <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg text-blue-800 text-sm mb-8">
            {project.status === 'modifying'
              ? 'Preparing to regenerate with your changes…'
              : 'Regenerating your app with the requested changes…'}
          </div>
        )}

        {project.status === 'deployment_failed' && (
          <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-800 text-sm mb-8">
            Regeneration failed.{project.error_message ? ` ${project.error_message}` : ''}
          </div>
        )}

        {project.status === 'deployed' && (
          <div className="border-t border-slate-200 pt-8">
            <h2 className="text-lg font-semibold text-slate-900 mb-1">Request Changes</h2>
            <p className="text-sm text-slate-500 mb-4">
              Describe what you want to add, remove, or change in the app.
            </p>
            <textarea
              value={changeRequest}
              onChange={(e) => setChangeRequest(e.target.value)}
              placeholder="e.g. Add a dark mode toggle, change the dashboard layout…"
              rows={5}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent resize-none"
            />
            <button
              onClick={handleRequestChanges}
              disabled={isSubmitting}
              className="btn-primary mt-4 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSubmitting ? 'Processing…' : 'Request Changes'}
            </button>
          </div>
        )}

        {project.modification_round != null && project.modification_round > 0 && (
          <p className="text-xs text-slate-400 mt-8">
            Modification round: {project.modification_round_completed ?? 0} / {project.modification_round}
          </p>
        )}
      </main>
    </div>
  )
}
