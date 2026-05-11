import { api } from './client'

export async function triggerStructuring(projectId) {
  const r = await api.post(`/projects/${projectId}/structure`)
  return r.data
}

export async function getStructured(projectId) {
  try {
    const r = await api.get(`/projects/${projectId}/structured`)
    return r.data
  } catch (err) {
    if (err.response?.status === 404) return null
    throw err
  }
}

export async function getClarifications(projectId) {
  try {
    const r = await api.get(`/projects/${projectId}/clarifications`)
    return r.data
  } catch (err) {
    if (err.response?.status === 404) return []
    throw err
  }
}

export async function answerClarification(projectId, ambiguityId, answer) {
  const r = await api.post(
    `/projects/${projectId}/clarifications/${ambiguityId}/answer`,
    { answer },
  )
  return r.data
}
