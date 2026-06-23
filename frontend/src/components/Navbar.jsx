import { useState, useRef, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Settings, LogOut, ExternalLink } from 'lucide-react'

import Logo from '@/components/Logo'
import ThemeToggle from '@/components/ThemeToggle'
import { useAuth } from '@/context/AuthContext'

function ProfileMenu() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  if (!user) return null

  const initial = (user.displayName || user.email || '?')[0].toUpperCase()

  async function handleLogout() {
    setOpen(false)
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-10 h-10 rounded-full overflow-hidden
                   bg-gradient-to-br from-accent to-accent-secondary
                   flex items-center justify-center
                   ring-2 ring-transparent hover:ring-accent/40
                   transition-all duration-200 shadow-md"
        aria-label="Open profile menu"
      >
        {user.photoURL ? (
          <img src={user.photoURL} alt="" className="w-full h-full object-cover" />
        ) : (
          <span className="text-white font-bold">{initial}</span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 w-72 rounded-2xl
                     bg-surface-panel border border-surface-border
                     shadow-2xl shadow-black/10 backdrop-blur-md
                     overflow-hidden animate-dropdown z-50"
        >
          {/* Header */}
          <div className="p-4 bg-gradient-to-br from-accent/10 to-accent-secondary/5
                          border-b border-surface-border">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-full overflow-hidden flex-shrink-0
                              bg-gradient-to-br from-accent to-accent-secondary
                              flex items-center justify-center">
                {user.photoURL ? (
                  <img src={user.photoURL} alt="" className="w-full h-full object-cover" />
                ) : (
                  <span className="text-white font-bold text-lg">{initial}</span>
                )}
              </div>
              <div className="min-w-0">
                <p className="font-semibold text-text-default truncate">
                  {user.displayName || 'User'}
                </p>
                <p className="text-xs text-text-muted truncate">{user.email}</p>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="p-2">
            <Link
              to="/profile"
              onClick={() => setOpen(false)}
              className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg
                         text-sm text-text-default hover:bg-accent/10 transition-colors"
            >
              <Settings className="w-4 h-4 text-text-muted" />
              Edit profile
              <ExternalLink className="w-3 h-3 ml-auto opacity-50" />
            </Link>
            <button
              onClick={handleLogout}
              className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg
                         text-sm text-error hover:bg-error/10 transition-colors"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Navbar() {
  return (
    <header className="sticky top-0 z-50 backdrop-blur-md bg-surface-panel/80 border-b border-surface-border shadow-sm">
      <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
        {/* Brand */}
        <Link to="/welcome" className="flex items-center gap-3">
          <Logo size={56} hoverable />
          <span className="text-2xl font-black tracking-tight
                           bg-gradient-to-r from-accent to-accent-secondary
                           bg-clip-text text-transparent
                           hidden sm:block">
            DeMaestro
          </span>
        </Link>

        {/* Right side */}
        <div className="flex items-center gap-4">
          <Link
            to="/about"
            className="text-sm font-medium text-text-muted hover:text-text-default transition-colors hidden sm:inline"
          >
            About Us
          </Link>
          <ThemeToggle />
          <ProfileMenu />
        </div>
      </div>
    </header>
  )
}
