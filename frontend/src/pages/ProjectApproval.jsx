import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Markdown from 'react-markdown'
import { toast } from 'sonner'
import { ArrowLeft, CheckCircle2, Database, Layout, Loader2, Users } from 'lucide-react'

import Logo from '../components/Logo'
import { approveProject } from '../api/approval'
import { getProject } from '../api/projects'

export default function ProjectApproval() {
  const { id: projectId } = useParams()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState('summary')

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => getProject(projectId),
    refetchInterval: (query) => (query.state.data?.summary ? false : 3000),
  })

  const approveMut = useMutation({
    mutationFn: () => approveProject(projectId),
    onSuccess: async () => {
      toast.success('Project approved!')
      await qc.invalidateQueries({ queryKey: ['project', projectId] })
    },
    onError: (err) => toast.error(err.message || 'Approval failed. Please try again.'),
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    )
  }

  const isApproved = !!project?.approved_at
  const isGenerating = project?.status === 'awaiting_approval' && (!project?.summary || !project?.blueprint)
  const canApprove = project?.status === 'awaiting_approval' && !!project?.summary && !!project?.blueprint

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

        {/* Generating state — shown while summary/blueprint are being built */}
        {isGenerating && (
          <div className="card flex flex-col items-center gap-3 py-10 text-center">
            <Loader2 className="w-8 h-8 animate-spin text-primary-500" />
            <p className="text-slate-700 font-medium">Generating your summary and blueprint…</p>
            <p className="text-sm text-slate-500">
              This usually takes a few seconds. The page will update automatically.
            </p>
          </div>
        )}

        {/* Tab content — only shown once content is ready */}
        {!isGenerating && activeTab === 'summary' && (
          <div className="card prose prose-slate max-w-none">
            {project?.summary ? (
              <Markdown>{project.summary}</Markdown>
            ) : (
              <p className="text-slate-500 not-prose">No summary available yet.</p>
            )}
          </div>
        )}

        {!isGenerating && activeTab === 'blueprint' && (
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
            ) : isGenerating ? (
              'Generating…'
            ) : (
              'Approve & Generate →'
            )}
          </button>
        </div>
      </main>
    </div>
  )
}

function renderDatabaseInFriendlyLanguage(tables) {
  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4">
        <Database className="w-5 h-5 text-primary-600" />
        <h2 className="text-base font-semibold text-slate-800">What the System Remembers</h2>
      </div>
      <p className="text-sm text-slate-500 mb-4">
        Behind the scenes, the app stores information so everything is saved and ready when you come back.
      </p>
      <div className="space-y-4">
        {(tables ?? []).map((table) => {
          const columnNames = (table.columns ?? []).map((c) => c.name).filter(
            (n) => !['id', 'created_at', 'updated_at'].includes(n)
          )
          const readable = columnNames.map(toReadableColumn).join(', ')
          return (
            <div key={table.name} className="border-l-2 border-primary-100 pl-4">
              <p className="text-sm font-semibold text-slate-800 mb-0.5">{toReadableTableName(table.name)}</p>
              <p className="text-sm text-slate-600">
                {readable
                  ? `The system remembers: ${readable}.`
                  : table.description}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function renderRoutesInFriendlyLanguage(routes) {
  const actions = (routes ?? []).map((route) => routeToAction(route)).filter(Boolean)
  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4">
        <Users className="w-5 h-5 text-primary-600" />
        <h2 className="text-base font-semibold text-slate-800">What Users Can Do</h2>
      </div>
      <p className="text-sm text-slate-500 mb-4">
        Here are the things users will be able to do inside the app.
      </p>
      <ul className="space-y-2">
        {actions.map((action, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
            <span className="mt-1 w-1.5 h-1.5 rounded-full bg-primary-400 flex-shrink-0" />
            {action}
          </li>
        ))}
      </ul>
    </div>
  )
}

function renderPagesInFriendlyLanguage(pages) {
  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-4">
        <Layout className="w-5 h-5 text-primary-600" />
        <h2 className="text-base font-semibold text-slate-800">Screens Users Will See</h2>
      </div>
      <p className="text-sm text-slate-500 mb-4">
        The app will have these screens, each designed to make things simple and clear.
      </p>
      <ul className="space-y-3">
        {(pages ?? []).map((page) => (
          <li key={page.name} className="flex items-start gap-2 text-sm">
            <span className="mt-1 w-1.5 h-1.5 rounded-full bg-primary-400 flex-shrink-0" />
            <span>
              <span className="font-semibold text-slate-800">{page.name}</span>
              <span className="text-slate-500"> — </span>
              <span className="text-slate-700">{simplifyPurpose(page.purpose)}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// Helpers

function toReadableTableName(name) {
  return name.replace(/([A-Z])/g, ' $1').trim()
}

function toReadableColumn(colName) {
  const map = {
    email: 'email address',
    name: 'name',
    created_at: 'join date',
    updated_at: 'last updated',
    user_id: 'which user it belongs to',
    friend_id: 'the friend',
    duration_minutes: 'how long it lasted',
    calories_burned: 'calories burned',
    date: 'the date',
    type: 'what type it was',
    notes: 'personal notes',
    title: 'title',
    target_date: 'target date',
    completed: 'whether it was completed',
    password_hash: 'password (stored securely)',
    avatar_url: 'profile photo',
  }
  return map[colName] ?? colName.replace(/_/g, ' ')
}

function routeToAction(route) {
  const desc = route.description?.toLowerCase() ?? ''
  const method = route.method?.toUpperCase()

  // Map common descriptions to friendly actions
  const friendlyMap = [
    [/create.*account|register/i, 'User can create a new account'],
    [/sign.?in|log.?in/i, 'User can sign in to their account'],
    [/sign.?out|log.?out/i, 'User can sign out'],
    [/list.*workout|view.*workout|get.*workout/i, 'User can view all their past workouts'],
    [/add.*workout|log.*workout|create.*workout/i, 'User can log a new workout'],
    [/update.*workout|edit.*workout/i, "User can update a workout's details"],
    [/delete.*workout|remove.*workout/i, 'User can delete a workout if they change their mind'],
    [/list.*goal|view.*goal|get.*goal/i, 'User can view their fitness goals'],
    [/add.*goal|create.*goal/i, 'User can set a new fitness goal'],
    [/update.*goal|complete.*goal/i, 'User can mark a goal as completed'],
    [/delete.*goal/i, 'User can remove a goal'],
    [/friend|follow/i, 'User can see friends and their recent activity'],
    [/progress|stat|chart/i, 'User can view progress charts and statistics'],
    [/export/i, 'User can export their data'],
    [/profile|account/i, 'User can update their profile information'],
    [/search/i, 'User can search through their history'],
    [/notification|reminder/i, 'User can manage notifications and reminders'],
  ]

  for (const [pattern, action] of friendlyMap) {
    if (pattern.test(desc) || pattern.test(route.path ?? '')) {
      return action
    }
  }

  // Fallback: build from method + description
  const verbMap = { GET: 'view', POST: 'add', PUT: 'update', PATCH: 'update', DELETE: 'delete' }
  const verb = verbMap[method] ?? 'use'
  const cleaned = route.description
    ? route.description.charAt(0).toLowerCase() + route.description.slice(1)
    : route.path
  return `User can ${verb} ${cleaned}`
}

function simplifyPurpose(purpose) {
  if (!purpose) return ''
  // Strip technical jargon patterns
  return purpose
    .replace(/authenticated users/gi, 'users who are signed in')
    .replace(/main entry point/gi, 'home screen')
    .replace(/CRUD/gi, 'manage')
    .replace(/endpoint/gi, '')
    .replace(/API/gi, '')
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
      {renderDatabaseInFriendlyLanguage(blueprint.database_schema)}
      {renderRoutesInFriendlyLanguage(blueprint.api_routes)}
      {renderPagesInFriendlyLanguage(blueprint.frontend_pages)}
    </div>
  )
}
