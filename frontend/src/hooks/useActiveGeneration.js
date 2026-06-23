import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

const ACTIVE_STATUSES = new Set([
  'generating', 'testing', 'debugging', 'deploying',
  'blueprinting', 'packaging', 'modifying', 'regenerating', 'verifying',
])

/**
 * Polls for the user's currently active project (if any).
 * Returns { project: {...} } or { project: null } when nothing is running.
 * Stops polling when no user is signed in.
 */
export function useActiveGeneration() {
  return useQuery({
    queryKey: ['activeGeneration'],
    queryFn: async () => {
      const { data } = await api.get('/projects/active')
      return data
    },
    refetchInterval: (query) => {
      // Keep polling while something is active; slow-poll when idle
      const proj = query.state.data?.project
      return proj ? 3000 : 10000
    },
    staleTime: 1000,
  })
}

export { ACTIVE_STATUSES }
