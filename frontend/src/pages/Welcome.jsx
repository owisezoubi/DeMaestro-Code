import { useNavigate, Link } from 'react-router-dom'
import { ArrowRight, FolderOpen, Sparkles, Users, BookOpen, Code, Rocket } from 'lucide-react'

import { useAuth } from '../context/AuthContext'

export default function Welcome() {
  const { user } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen bg-surface-page flex flex-col">
      <main className="flex-1 flex flex-col items-center justify-center text-center px-4 py-12">
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

        {/* ── About Us CTA ─────────────────────────────────────────────── */}
        <div className="relative w-full max-w-3xl rounded-3xl overflow-hidden
                        border border-surface-border
                        bg-gradient-to-br from-accent/10 via-surface-panel to-accent-secondary/10">
          {/* Ornament blobs */}
          <div className="absolute -left-12 -top-12 w-48 h-48 rounded-full bg-accent/20 blur-3xl pointer-events-none" />
          <div className="absolute -left-6 bottom-0 w-32 h-32 rounded-full bg-accent-secondary/15 blur-2xl pointer-events-none" />
          <div className="absolute -right-12 -bottom-12 w-56 h-56 rounded-full bg-accent-secondary/20 blur-3xl pointer-events-none" />
          <div className="absolute -right-4 top-1/3 w-28 h-28 rounded-full bg-accent/15 blur-2xl pointer-events-none" />

          {/* Floating icons — left */}
          <div className="absolute left-6 top-1/2 -translate-y-1/2 hidden md:flex flex-col gap-4 pointer-events-none">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-accent to-accent-secondary flex items-center justify-center shadow-lg animate-float" style={{ animationDuration: '8s' }}>
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center shadow-lg animate-float ml-4" style={{ animationDuration: '10s', animationDelay: '1.5s' }}>
              <Code className="w-5 h-5 text-white" />
            </div>
          </div>

          {/* Floating icons — right */}
          <div className="absolute right-6 top-1/2 -translate-y-1/2 hidden md:flex flex-col gap-4 pointer-events-none">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center shadow-lg animate-float" style={{ animationDuration: '9s', animationDelay: '0.7s' }}>
              <Rocket className="w-5 h-5 text-white" />
            </div>
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-amber-500 to-rose-500 flex items-center justify-center shadow-lg animate-float" style={{ animationDuration: '11s', animationDelay: '2s' }}>
              <Users className="w-5 h-5 text-white" />
            </div>
          </div>

          {/* Center content */}
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
