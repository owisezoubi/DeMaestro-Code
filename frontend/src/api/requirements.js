import { api } from './client'

export async function submitTextRequirements(projectId, content) {
  const r = await api.post(`/projects/${projectId}/requirements/text`, { content })
  return r.data
}

export async function submitPdfRequirements(projectId, file) {
  const formData = new FormData()
  formData.append('file', file)
  const r = await api.post(`/projects/${projectId}/requirements/pdf`, formData)
  return r.data
}

export async function listRequirements(projectId) {
  const r = await api.get(`/projects/${projectId}/requirements`)
  return r.data
}

export async function getProjectStatus(projectId) {
  const r = await api.get(`/projects/${projectId}/status`)
  return r.data
}
