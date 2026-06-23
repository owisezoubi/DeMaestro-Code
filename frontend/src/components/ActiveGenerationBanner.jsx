import { Link, useLocation } from 'react-router-dom'
import { useActiveGeneration } from '../hooks/useActiveGeneration'

/**
 * Thin banner shown at the top of every page while a generation is running.
 * Hidden on /projects/:id/* pages (they show their own progress UI).
 * Auto-hides when no project is active.
 */
export function ActiveGenerationBanner() {
  const location = useLocation()
  const { data } = useActiveGeneration()

  // Don't show on any project-detail route — they have inline progress
  const onProjectPage = /^\/projects\/[^/]+/.test(location.pathname)
  if (onProjectPage) return null

  const proj = data?.project
  if (!proj) return null

  const progress =
    proj.total_files && proj.total_files > 0
      ? `${proj.generated_count ?? 0} / ${proj.total_files} files`
      : proj.current_stage
      ? proj.current_stage.replace(/_/g, ' ')
      : 'starting…'

  return (
    <div className="bg-accent/10 border-b border-accent/30 px-4 py-2 text-sm">
      <div className="max-w-7xl mx-auto flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
          </span>
          <span className="text-text-default">
            Building{' '}
            <strong className="font-semibold">{proj.name || 'project'}</strong>
            <span className="text-text-muted ml-2">· {progress}</span>
          </span>
        </div>
        <Link
          to={`/projects/${proj.id}/generation`}
          className="text-accent hover:underline font-medium shrink-0"
        >
          View progress →
        </Link>
      </div>
    </div>
  )
}
