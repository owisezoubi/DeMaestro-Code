import { api } from './client'

export async function listProjects() {
  const r = await api.get('/projects')
  return r.data
}

export async function createProject(name) {
  const r = await api.post('/projects', { name })
  return r.data
}

export async function getProject(id) {
  const r = await api.get(`/projects/${id}`)
  return r.data
}

export async function deleteProject(id) {
  await api.delete(`/projects/${id}`)
}

export async function ensureUserProfile() {
  const r = await api.get('/auth/me')
  return r.data
}
