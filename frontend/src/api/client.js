import axios from 'axios'
import { auth } from '../auth/firebase'

const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export const api = axios.create({
  baseURL,
  timeout: 60000,
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

// Retry once on network errors and timeouts. The pipeline spawns
// subprocesses that can briefly block responses; one retry smooths
// over transient blocks without masking real backend errors.
api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const cfg = err.config
    if (!cfg || cfg._retried) return Promise.reject(err)
    const shouldRetry =
      err.code === 'ECONNABORTED' ||
      err.code === 'ERR_NETWORK' ||
      err.message?.includes('timeout')
    if (shouldRetry) {
      cfg._retried = true
      await new Promise((r) => setTimeout(r, 800))
      return api(cfg)
    }
    return Promise.reject(err)
  },
)
