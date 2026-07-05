import { FolderOpen } from 'lucide-react'

export default function ProjectCardSkeleton({ delay = 0 }) {
  return (
    <div
      className="relative overflow-hidden p-5 rounded-xl
                 bg-surface-panel border border-surface-border
                 animate-fade-in"
      style={{ animationDelay: `${delay}ms` }}
    >
      {/* Base skeleton shapes */}
      <div className="flex items-start justify-between mb-3">
        <div className="w-8 h-8 rounded-lg bg-surface-border/70" />
        <div className="w-16 h-5 rounded-full bg-surface-border/70" />
      </div>
      <div className="h-4 w-3/4 rounded bg-surface-border/70 mb-2" />
      <div className="h-3 w-1/2 rounded bg-surface-border/50" />

      {/* Shimmer sweep */}
      <div className="absolute inset-0 pointer-events-none
                      bg-gradient-to-r from-transparent
                      via-accent/10 to-transparent
                      -translate-x-full animate-shimmer-sweep" />

      {/* Subtle folder icon behind the skeleton */}
      <FolderOpen className="absolute -right-3 -bottom-3 w-16 h-16
                             text-accent/5 pointer-events-none" />
    </div>
  )
}
