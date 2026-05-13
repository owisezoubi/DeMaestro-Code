import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Markdown from 'react-markdown'
import { toast } from 'sonner'
import { ArrowLeft, CheckCircle2, Loader2 } from 'lucide-react'

import Logo from '../components/Logo'
import { approveProject } from '../api/approval'
import { getProject } from '../api/projects'

export default function ProjectApproval() {
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState('summary')

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => getProject(projectId),
    refetchInterval: (query) => (query.state.data?.approved_at ? false : 3000),
  })

  const approveMut = useMutation({
    mutationFn: () => approveProject(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      toast.success('Project approved!')
      navigate(`/projects/${projectId}`)
    },
    onError: (err) => toast.error(err.friendlyMessage || 'Approval failed. Please try again.'),
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    )
  }

  const isApproved = !!project?.approved_at
  const canApprove = project?.status === 'awaiting_approval'

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <Logo />
          <Link to={`/projects/${projectId}`} className="btn-secondary text-sm">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back
          </Link>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6 space-y-4">
        {/* Title + approval banner */}
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold text-slate-900">
            Review: {project?.name}
          </h1>
          {isApproved && (
            <span className="text-sm text-emerald-600 font-medium flex items-center gap-1">
              <CheckCircle2 className="w-4 h-4" />
              Approved {new Date(project.approved_at).toLocaleDateString()}
            </span>
          )}
        </div>

        {/* Tabs */}
        <div className="flex gap-2 border-b border-slate-200">
          {['summary', 'blueprint'].map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === tab
                  ? 'border-primary-600 text-primary-700'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {activeTab === 'summary' && (
          <div className="card prose prose-slate max-w-none">
            {project?.summary ? (
              <Markdown>{project.summary}</Markdown>
            ) : (
              <p className="text-slate-500 not-prose">No summary available yet.</p>
            )}
          </div>
        )}

        {activeTab === 'blueprint' && (
          <BlueprintView blueprint={project?.blueprint} />
        )}

        {/* Footer actions */}
        <div className="flex gap-3 justify-end pt-2">
          <button
            className="btn-secondary"
            onClick={() =>
              toast.info('Request Changes will be available in a future update.')
            }
          >
            Request Changes
          </button>
          <button
            className="btn-primary"
            disabled={approveMut.isPending || !canApprove}
            onClick={() => approveMut.mutate()}
          >
            {approveMut.isPending ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Approving…
              </>
            ) : isApproved ? (
              'Already Approved'
            ) : (
              'Approve & Generate →'
            )}
          </button>
        </div>
      </main>
    </div>
  )
}

function BlueprintView({ blueprint }) {
  if (!blueprint) {
    return (
      <div className="card">
        <p className="text-slate-500">No blueprint available yet.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Section title="Database Schema">
        <div className="space-y-3">
          {(blueprint.database_schema ?? []).map((table) => (
            <div key={table.name} className="card">
              <div className="mb-2">
                <span className="font-semibold text-slate-800">{table.name}</span>
                <p className="text-sm text-slate-500">{table.description}</p>
              </div>
              <table className="w-full text-sm text-left border-collapse">
                <thead>
                  <tr className="text-xs text-slate-500 uppercase bg-slate-50">
                    <th className="px-3 py-2 border border-slate-100">Column</th>
                    <th className="px-3 py-2 border border-slate-100">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {(table.columns ?? []).map((col, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="px-3 py-1.5 font-mono text-slate-700 border border-slate-100">
                        {col.name}
                      </td>
                      <td className="px-3 py-1.5 text-slate-600 border border-slate-100">
                        {col.type}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      </Section>

      <Section title="API Routes">
        <div className="card divide-y divide-slate-100">
          {(blueprint.api_routes ?? []).map((route, i) => (
            <div key={i} className="py-3 first:pt-0 last:pb-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-bold font-mono bg-primary-50 text-primary-700 px-2 py-0.5 rounded">
                  {route.method}
                </span>
                <span className="font-mono text-sm text-slate-800">{route.path}</span>
              </div>
              <p className="text-sm text-slate-600">{route.description}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Frontend Pages">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {(blueprint.frontend_pages ?? []).map((page) => (
            <div key={page.name} className="card">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-slate-800">{page.name}</span>
                <span className="text-xs font-mono text-slate-400">{page.route}</span>
              </div>
              <p className="text-sm text-slate-600 mb-2">{page.purpose}</p>
              <div className="flex flex-wrap gap-1">
                {(page.key_components ?? []).map((c) => (
                  <span
                    key={c}
                    className="text-xs bg-slate-100 text-slate-600 rounded px-1.5 py-0.5"
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Section>

      {blueprint.technology_stack_notes && (
        <p className="text-sm text-slate-500 italic">{blueprint.technology_stack_notes}</p>
      )}
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div>
      <h2 className="text-base font-semibold text-slate-800 mb-3">{title}</h2>
      {children}
    </div>
  )
}
