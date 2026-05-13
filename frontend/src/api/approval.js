import { api } from './client'

export async function approveProject(projectId) {
  const r = await api.post(`/projects/${projectId}/approve`)
  return r.data
}
