import { useNavigate, Link } from 'react-router-dom'
import { ArrowRight, FolderOpen, Sparkles, Users, BookOpen, Code, Rocket } from 'lucide-react'

import { useAuth } from '../context/AuthContext'
import ExampleShowcase from '../components/ExampleShowcase'

const EXAMPLES = [
  {
    id: 'todo',
    title: 'FocusList',
    tagline: 'A quiet home for tasks that actually matter.',
    prompt: '"A todo app where I can group tasks by project."',
    tags: ['Personal productivity', 'Auth', 'Full-stack'],
    accent: 'from-violet-500 to-fuchsia-500',
    accentGlow: 'shadow-violet-500/25',
    summary:
      'FocusList takes a familiar todo pattern and organises it around real projects — not one giant list of items. Users sign in, group tasks by client or context, and see what actually needs doing today. Every task can carry a due date, priority, and small notes; completed tasks slide off into a calm weekly review.',
    features: [
      'Task grouping by project',
      'Due dates and priority tags',
      'Weekly completion trend',
      'Email + password authentication',
    ],
    screenshots: [
      '/examples/focuslist/01-dashboard.png',
      '/examples/focuslist/02-add-task.png',
      '/examples/focuslist/03-project-view.png',
      '/examples/focuslist/04-completed.png',
    ],
  },
  {
    id: 'portfolio',
    title: 'Solstice',
    tagline: 'A dark, editorial portfolio for people who make things.',
    prompt: '"A dark-themed portfolio site with projects and contact form."',
    tags: ['Portfolio', 'Public', 'Dark mode'],
    accent: 'from-slate-700 to-slate-900',
    accentGlow: 'shadow-slate-800/40',
    summary:
      'Solstice is a public-facing portfolio built for designers and engineers who want their work to feel considered. Every project has its own detail page, an about section shows the person behind the work, and a friendly contact form gets messages to the inbox. No signup, no dashboards — just a page that reads well.',
    features: [
      'Project gallery with detail pages',
      'Editorial dark typography',
      'Contact form with backend delivery',
      'Fully public, no auth required',
    ],
    screenshots: [
      '/examples/solstice/01-home.png',
      '/examples/solstice/02-projects.png',
      '/examples/solstice/03-contact.png',
    ],
  },
  {
    id: 'recipes',
    title: 'Bistro 75',
    tagline: 'A warm cookbook for the recipes you actually cook.',
    prompt: '"A recipe manager with favorites and search."',
    tags: ['Recipes', 'Search', 'Personal'],
    accent: 'from-amber-500 to-orange-600',
    accentGlow: 'shadow-amber-500/25',
    summary:
      'Bistro 75 gives every household a private cookbook. Users save recipes with ingredients and step-by-step instructions, mark the ones they love as favorites, and find anything again with a search that understands both dish names and ingredients. The whole thing feels like a slow food magazine, not a database.',
    features: [
      'Save and organise recipes',
      'Favorite the ones you return to',
      'Search by name or ingredient',
      'Editorial cookbook typography',
    ],
    screenshots: [
      '/examples/bistro/01-menu.png',
      '/examples/bistro/02-recipe.png',
      '/examples/bistro/03-favorites.png',
      '/examples/bistro/04-search.png',
    ],
  },
  {
    id: 'notes',
    title: 'Inkwell',
    tagline: 'A minimalist notepad for the ideas that stick.',
    prompt: '"A minimalist note-taking app with tags and dark mode."',
    tags: ['Notes', 'Auth', 'Dark mode'],
    accent: 'from-emerald-500 to-teal-600',
    accentGlow: 'shadow-emerald-500/25',
    summary:
      'Inkwell is a note-taking app that gets out of the way. Users sign in, jot down thoughts across a three-column layout, tag anything so it stays discoverable, and slip between light and dark modes without breaking flow. No blocks, no databases, no ceremony — just a place to write.',
    features: [
      'Three-column notes layout',
      'Custom tags per note',
      'Light and dark modes',
      'Auto-save with word count',
    ],
    screenshots: [
      '/examples/inkwell/01-notes.png',
      '/examples/inkwell/02-editor.png',
      '/examples/inkwell/03-tags.png',
    ],
  },
]

export default function Welcome() {
  const { user } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen bg-surface-page flex flex-col relative">
      {/* Ambient blobs */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-0 -left-40 w-96 h-96 rounded-full
                        bg-accent/10 blur-3xl animate-pulse-slow" />
        <div
          className="absolute top-1/3 -right-40 w-96 h-96 rounded-full
                     bg-accent-secondary/10 blur-3xl animate-pulse-slow"
          style={{ animationDelay: '3s' }}
        />
      </div>

      <main className="flex-1 relative flex flex-col items-center justify-center text-center px-4 py-12">
        <h1 className="text-4xl sm:text-5xl font-bold text-text-default mb-3">
          Welcome, {user?.displayName || user?.email?.split('@')[0] || 'there'}!
        </h1>
        <p className="text-lg text-slate-600 dark:text-slate-300 max-w-xl mb-8">
          Turn your requirements into a complete, runnable web app. Describe what you
          want, answer a few quick questions, and DeMaestro designs, builds, and
          packages it for you.
        </p>
        <div className="flex flex-wrap gap-3 justify-center mb-16">
          <button onClick={() => navigate('/dashboard')} className="btn-primary text-base px-6 py-3">
            <FolderOpen className="w-5 h-5 mr-2" />
            Go to my projects
            <ArrowRight className="w-5 h-5 ml-2" />
          </button>
          <button onClick={() => navigate('/projects/new')} className="btn-secondary text-base px-6 py-3">
            Start a new project
          </button>
        </div>

        {/* ── Rich examples showcase ─────────────────────────────────── */}
        <section className="w-full max-w-6xl mx-auto mb-24">

          {/* Section header */}
          <div className="text-center mb-16">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full
                            bg-accent/10 border border-accent/20
                            text-xs font-semibold text-accent uppercase tracking-widest mb-5">
              <Sparkles className="w-3.5 h-3.5" />
              Real DeMaestro examples
            </div>
            <h2 className="text-4xl md:text-5xl font-black text-text-default mb-4 leading-tight">
              Apps built{' '}
              <span className="bg-gradient-to-r from-accent to-accent-secondary
                               bg-clip-text text-transparent">
                from a single sentence
              </span>
            </h2>
            <p className="text-lg text-text-muted max-w-2xl mx-auto leading-relaxed">
              Four very different apps, each described in one line and shipped
              end-to-end by DeMaestro. Scroll through the screens, read what
              each one does, and picture yours in the same lineup.
            </p>
          </div>

          {/* Alternating showcase rows */}
          <div className="space-y-24">
            {EXAMPLES.map((ex, i) => (
              <ExampleShowcase key={ex.id} example={ex} index={i} />
            ))}
          </div>

          {/* Trailing CTA */}
          <div className="mt-20 text-center">
            <div className="inline-flex items-center gap-3 flex-wrap justify-center">
              <button
                onClick={() => navigate('/projects/new')}
                className="group inline-flex items-center gap-2 px-8 py-4 rounded-2xl
                           bg-gradient-to-r from-accent to-accent-secondary
                           text-white font-bold text-sm
                           shadow-2xl shadow-accent/30
                           hover:shadow-2xl hover:shadow-accent/45
                           hover:scale-[1.03] active:scale-[0.98]
                           transition-all duration-200"
              >
                Describe yours in one sentence
                <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </button>
            </div>
            <p className="text-xs text-text-muted mt-4">
              No coding required · Live URL in minutes
            </p>
          </div>

        </section>

        {/* ── About Us CTA ─────────────────────────────────────────────── */}
        <div className="relative w-full max-w-3xl rounded-3xl overflow-hidden
                        border border-surface-border
                        bg-gradient-to-br from-accent/10 via-surface-panel to-accent-secondary/10">
          <div className="absolute -left-12 -top-12 w-48 h-48 rounded-full bg-accent/20 blur-3xl pointer-events-none" />
          <div className="absolute -left-6 bottom-0 w-32 h-32 rounded-full bg-accent-secondary/15 blur-2xl pointer-events-none" />
          <div className="absolute -right-12 -bottom-12 w-56 h-56 rounded-full bg-accent-secondary/20 blur-3xl pointer-events-none" />
          <div className="absolute -right-4 top-1/3 w-28 h-28 rounded-full bg-accent/15 blur-2xl pointer-events-none" />

          <div className="absolute left-6 top-1/2 -translate-y-1/2 hidden md:flex flex-col gap-4 pointer-events-none">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-accent to-accent-secondary flex items-center justify-center shadow-lg animate-float" style={{ animationDuration: '8s' }}>
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center shadow-lg animate-float ml-4" style={{ animationDuration: '10s', animationDelay: '1.5s' }}>
              <Code className="w-5 h-5 text-white" />
            </div>
          </div>

          <div className="absolute right-6 top-1/2 -translate-y-1/2 hidden md:flex flex-col gap-4 pointer-events-none">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center shadow-lg animate-float" style={{ animationDuration: '9s', animationDelay: '0.7s' }}>
              <Rocket className="w-5 h-5 text-white" />
            </div>
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-amber-500 to-rose-500 flex items-center justify-center shadow-lg animate-float" style={{ animationDuration: '11s', animationDelay: '2s' }}>
              <Users className="w-5 h-5 text-white" />
            </div>
          </div>

          <div className="relative px-8 md:px-32 py-14 text-center">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full
                            bg-surface-panel/70 backdrop-blur border border-surface-border
                            text-xs font-semibold text-accent uppercase tracking-wider mb-5">
              <BookOpen className="w-3.5 h-3.5" />
              About the project
            </div>
            <h2 className="text-3xl md:text-4xl font-black text-text-default mb-4 leading-tight">
              Discover the story behind{' '}
              <br className="hidden sm:block" />
              <span className="bg-gradient-to-r from-accent to-accent-secondary bg-clip-text text-transparent">
                DeMaestro
              </span>
            </h2>
            <p className="text-text-muted max-w-xl mx-auto mb-8 leading-relaxed">
              Learn about the multi-agent architecture, the team behind it,
              and the academic supervision that shaped the project.
            </p>
            <Link
              to="/about"
              className="group inline-flex items-center gap-2 px-7 py-3.5 rounded-xl
                         bg-gradient-to-r from-accent to-accent-secondary
                         text-white font-bold shadow-xl shadow-accent/30
                         hover:shadow-2xl hover:shadow-accent/40
                         hover:scale-105 active:scale-95
                         transition-all duration-200"
            >
              About Us
              <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
            </Link>
          </div>
        </div>
      </main>
    </div>
  )
}
