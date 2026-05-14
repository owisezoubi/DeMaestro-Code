import { api } from './client'

export async function startGeneration(projectId) {
  const r = await api.post(`/projects/${projectId}/generate`)
  return r.data
}

export async function getGenerationStatus(projectId) {
  const r = await api.get(`/projects/${projectId}/generation-status`)
  return r.data
}

export function downloadGeneratedCode(projectId) {
  // Opens the download ZIP endpoint in a new tab
  window.open(`${api.defaults.baseURL}/projects/${projectId}/download`, '_blank')
}
