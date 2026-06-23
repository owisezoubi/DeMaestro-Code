import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Camera, ArrowLeft } from 'lucide-react'
import { toast } from 'sonner'
import {
  updateProfile,
  updateEmail,
  updatePassword,
  reauthenticateWithCredential,
  EmailAuthProvider,
} from 'firebase/auth'
import { ref, uploadBytes, getDownloadURL } from 'firebase/storage'

import { auth, storage } from '../auth/firebase'
import { useAuth } from '../context/AuthContext'

export default function ProfilePage() {
  const { user } = useAuth()

  const [username, setUsername] = useState(user?.displayName || '')
  const [email, setEmail] = useState(user?.email || '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [avatarFile, setAvatarFile] = useState(null)
  const [avatarPreview, setAvatarPreview] = useState(user?.photoURL || null)
  const [saving, setSaving] = useState(false)

  const initials = (username || email || '?')[0].toUpperCase()

  function handleAvatarChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setAvatarFile(file)
    const reader = new FileReader()
    reader.onload = (ev) => setAvatarPreview(ev.target.result)
    reader.readAsDataURL(file)
  }

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    try {
      const fbUser = auth.currentUser
      if (!fbUser) throw new Error('Not authenticated')

      // Re-authenticate if changing email or password (Firebase requires it)
      if ((email !== user.email || newPassword) && currentPassword) {
        const cred = EmailAuthProvider.credential(user.email, currentPassword)
        await reauthenticateWithCredential(fbUser, cred)
      }

      // Upload avatar to Firebase Storage if changed
      let photoURL = fbUser.photoURL
      if (avatarFile) {
        const storageRef = ref(storage, `users/${fbUser.uid}/avatar/${avatarFile.name}`)
        await uploadBytes(storageRef, avatarFile)
        photoURL = await getDownloadURL(storageRef)
      }

      // Update Firebase Auth profile (displayName + photoURL)
      await updateProfile(fbUser, {
        displayName: username || null,
        photoURL,
      })

      // Update email
      if (email && email !== user.email) {
        await updateEmail(fbUser, email)
      }

      // Update password
      if (newPassword) {
        if (newPassword.length < 6) throw new Error('New password must be at least 6 characters')
        await updatePassword(fbUser, newPassword)
      }

      toast.success('Profile updated')
      setCurrentPassword('')
      setNewPassword('')
      setAvatarFile(null)
    } catch (err) {
      const msg = err.code === 'auth/requires-recent-login'
        ? 'Enter your current password to change email or password.'
        : err.code === 'auth/wrong-password'
        ? 'Current password is incorrect.'
        : err.message || 'Save failed'
      toast.error(msg)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="min-h-screen bg-surface-page">
      <main className="max-w-2xl mx-auto px-6 py-12">
        <div className="mb-8">
          <Link
            to="/dashboard"
            className="inline-flex items-center gap-1.5 text-sm text-text-muted
                       hover:text-text-default transition-colors mb-6"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Dashboard
          </Link>
          <h1 className="text-3xl font-bold text-text-default mb-1">Profile</h1>
          <p className="text-text-muted">Manage your account settings.</p>
        </div>

        <form onSubmit={handleSave} className="space-y-8">
          {/* Avatar section */}
          <div className="flex items-center gap-6">
            <div className="relative w-24 h-24 rounded-full overflow-hidden flex-shrink-0
                            bg-gradient-to-br from-accent to-accent-secondary
                            flex items-center justify-center shadow-lg">
              {avatarPreview ? (
                <img src={avatarPreview} alt="Avatar" className="w-full h-full object-cover" />
              ) : (
                <span className="text-white text-3xl font-black">{initials}</span>
              )}
            </div>
            <div>
              <label className="inline-flex items-center gap-2 px-4 py-2 rounded-lg
                                border border-surface-border bg-surface-panel
                                text-text-default text-sm font-medium
                                hover:border-accent/50 cursor-pointer transition-all">
                <Camera className="w-4 h-4" />
                Upload picture
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleAvatarChange}
                  className="hidden"
                />
              </label>
              <p className="text-xs text-text-muted mt-1.5">JPG, PNG or GIF. Max 5 MB.</p>
            </div>
          </div>

          {/* Profile fields */}
          <div className="space-y-4 p-6 rounded-xl border border-surface-border bg-surface-panel">
            <h2 className="text-sm font-semibold text-text-default">Account info</h2>
            <ProfileField
              label="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Your display name"
            />
            <ProfileField
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>

          {/* Password change */}
          <div className="space-y-4 p-6 rounded-xl border border-surface-border bg-surface-panel">
            <div>
              <h2 className="text-sm font-semibold text-text-default">Change password</h2>
              <p className="text-xs text-text-muted mt-0.5">
                Leave blank to keep your current password. Current password required to change email too.
              </p>
            </div>
            <ProfileField
              label="Current password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder="Required to change email or password"
            />
            <ProfileField
              label="New password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="At least 6 characters"
            />
          </div>

          <button
            type="submit"
            disabled={saving}
            className="px-6 py-3 rounded-lg
                       bg-gradient-to-r from-accent to-accent-secondary
                       text-white font-semibold
                       hover:shadow-lg hover:shadow-accent/30
                       hover:scale-[1.02] active:scale-[0.98]
                       disabled:opacity-50 disabled:scale-100
                       transition-all duration-200
                       inline-flex items-center gap-2"
          >
            {saving && (
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            )}
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </form>
      </main>
    </div>
  )
}

function ProfileField({ label, ...props }) {
  return (
    <div>
      <label className="block text-sm font-medium text-text-default mb-1.5">{label}</label>
      <input
        {...props}
        className="w-full px-4 py-2.5 rounded-lg border border-surface-border
                   bg-surface-page text-text-default
                   placeholder:text-text-muted
                   focus:outline-none focus:ring-2 focus:ring-accent/50
                   focus:border-accent transition-all"
      />
    </div>
  )
}
