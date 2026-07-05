import { deleteProject } from '../api/projects'

/**
 * Delete a project AND immediately purge it from every related cache key
 * so any Dashboard mount after this call shows a clean list with no ghost card.
 * Fires a background invalidation to reconcile with the server.
 */
export async function deleteProjectAndPurge(qc, projectId) {
  await deleteProject(projectId)

  // Synchronous cache purge — done before any navigation happens
  qc.setQueryData(['projects'], (old) =>
    (old || []).filter((p) => p.id !== projectId)
  )
  qc.removeQueries({ queryKey: ['project', projectId] })
  qc.removeQueries({ queryKey: ['project-status', projectId] })
  qc.removeQueries({ queryKey: ['generationStatus', projectId] })
  qc.removeQueries({ queryKey: ['deploymentStatus', projectId] })
  qc.removeQueries({ queryKey: ['clarifications', projectId] })
  qc.removeQueries({ queryKey: ['clarification-progress', projectId] })

  // Background reconcile — non-blocking
  qc.invalidateQueries({ queryKey: ['projects'] })
}
