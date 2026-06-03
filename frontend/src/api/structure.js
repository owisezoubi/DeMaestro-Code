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
    { timeout: 300000 },
  )
  return r.data
}

export async function getClarificationHistory(projectId) {
  try {
    const r = await api.get(`/projects/${projectId}/clarification-history`)
    return r.data
  } catch (err) {
    if (err.response?.status === 404) return []
    throw err
  }
}

export async function answerClarificationsBatch(projectId, answers) {
  // answers: [{ ambiguity_id, answer }]
  const r = await api.post(
    `/projects/${projectId}/clarifications/answers`,
    { answers },
    { timeout: 600000 },
  )
  return r.data
}

export async function getClarificationProgress(projectId) {
  try {
    const r = await api.get(`/projects/${projectId}/clarifications/progress`)
    return r.data?.answers || {}
  } catch (err) {
    if (err.response?.status === 404) return {}
    throw err
  }
}

export async function saveClarificationProgress(projectId, answers) {
  const r = await api.put(
    `/projects/${projectId}/clarifications/progress`,
    { answers },
  )
  return r.data
}

export async function reviseRequirements(projectId, { answers, newRequirements }) {
  const r = await api.post(
    `/projects/${projectId}/requirements/revise`,
    { answers, new_requirements: newRequirements },
    { timeout: 300000 },
  )
  return r.data
}
