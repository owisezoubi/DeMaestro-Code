import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertCircle } from 'lucide-react'

import { useAuth } from '../context/AuthContext'
import Logo from '../components/Logo'
import ThemeToggle from '../components/ThemeToggle'
import AuthScene from '../components/AuthScene'

export default function Signup() {
  const { signup } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function onSubmit(e) {
    e.preventDefault()
    setError(null)
    if (password.length < 6) {
      setError('Password must be at least 6 characters')
      return
    }
    setBusy(true)
    try {
      await signup(email, password)
      navigate('/welcome', { replace: true })
    } catch (err) {
      setError(err.message || 'Signup failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex">
      {/* Left: form */}
      <div className="flex-1 flex flex-col items-center justify-center px-8 py-12 bg-surface-page relative">
        <div className="absolute top-4 right-4">
          <ThemeToggle />
        </div>
        <div className="w-full max-w-md space-y-8">
          <div>
            <Logo linked={false} className="mb-8" />
            <h1 className="text-3xl font-bold text-text-default">Create your account</h1>
            <p className="text-text-muted mt-2">Get started — it's free.</p>
          </div>

          <form onSubmit={onSubmit} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-text-default mb-1.5">
                Email
              </label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full px-4 py-3 rounded-lg border border-surface-border
                           bg-surface-panel text-text-default
                           placeholder:text-text-muted
                           focus:outline-none focus:ring-2 focus:ring-accent/50
                           focus:border-accent transition-all"
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-text-default mb-1.5">
                Password
              </label>
              <input
                type="password"
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-lg border border-surface-border
                           bg-surface-panel text-text-default
                           placeholder:text-text-muted
                           focus:outline-none focus:ring-2 focus:ring-accent/50
                           focus:border-accent transition-all"
                placeholder="At least 6 characters"
              />
            </div>

            {error && (
              <div
                role="alert"
                className="flex items-start gap-2 px-4 py-3 rounded-lg
                           bg-error/10 border border-error/20 text-error
                           text-sm animate-shake"
              >
                <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={busy}
              className="w-full py-3 rounded-lg
                         bg-gradient-to-r from-accent to-accent-secondary
                         text-white font-semibold
                         hover:shadow-lg hover:shadow-accent/30
                         hover:scale-[1.02] active:scale-[0.98]
                         disabled:opacity-60 disabled:scale-100
                         transition-all duration-200"
            >
              {busy ? 'Creating…' : 'Create account'}
            </button>
          </form>

          <p className="text-sm text-text-muted text-center">
            Already have an account?{' '}
            <Link to="/login" className="text-accent font-medium hover:underline">
              Sign in
            </Link>
          </p>
        </div>
      </div>

      {/* Right: animated scene */}
      <AuthScene />
    </div>
  )
}
