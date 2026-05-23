import { useNavigate } from 'react-router-dom'
import { LogOut, ArrowRight, FolderOpen } from 'lucide-react'

import Logo from '../components/Logo'
import ThemeToggle from '../components/ThemeToggle'
import { useAuth } from '../context/AuthContext'

export default function Welcome() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  async function onLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex flex-col">
      <header className="border-b border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-end gap-3">
          <span className="text-sm text-slate-600 dark:text-slate-400 hidden sm:inline">{user?.email}</span>
          <ThemeToggle />
          <button onClick={onLogout} className="btn-secondary">
            <LogOut className="w-4 h-4 mr-2" />
            Sign out
          </button>
        </div>
      </header>

      <main className="flex-1 flex flex-col items-center justify-center text-center px-4 py-12">
        <Logo imgClassName="h-28 w-auto" linked={false} className="mb-6" />
        <h1 className="text-4xl sm:text-5xl font-bold text-slate-900 dark:text-slate-100 mb-3">
          Welcome to DeMaestro
        </h1>
        <p className="text-lg text-slate-600 dark:text-slate-300 max-w-xl mb-8">
          Turn your requirements into a complete, runnable web app. Describe what you
          want, answer a few quick questions, and DeMaestro designs, builds, and
          packages it for you.
        </p>
        <div className="flex flex-wrap gap-3 justify-center">
          <button onClick={() => navigate('/dashboard')} className="btn-primary text-base px-6 py-3">
            <FolderOpen className="w-5 h-5 mr-2" />
            Go to my projects
            <ArrowRight className="w-5 h-5 ml-2" />
          </button>
          <button onClick={() => navigate('/projects/new')} className="btn-secondary text-base px-6 py-3">
            Start a new project
          </button>
        </div>
      </main>
    </div>
  )
}
