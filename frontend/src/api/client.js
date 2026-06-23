import axios from 'axios'
import { auth } from '../auth/firebase'

// In development Vite proxies /auth, /projects, /health → localhost:8000,
// so we use an empty baseURL (relative URLs) to avoid any cross-origin requests.
// In production set VITE_API_BASE_URL to your deployed API origin.
const baseURL = import.meta.env.VITE_API_BASE_URL ?? ''

export const api = axios.create({
  baseURL,
  timeout: 30000,
})

// Attach Firebase ID token to every request automatically.
api.interceptors.request.use(async (config) => {
  const user = auth.currentUser
  if (user) {
    const token = await user.getIdToken()
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Surface backend errors in a friendly shape.
api.interceptors.response.use(
  (r) => r,
  (err) => {
    const message =
      err.response?.data?.detail ||
      err.response?.data?.message ||
      err.message ||
      'Request failed'
    err.friendlyMessage = message
    return Promise.reject(err)
  },
)
