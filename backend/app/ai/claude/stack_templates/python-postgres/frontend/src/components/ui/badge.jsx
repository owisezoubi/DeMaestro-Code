import * as React from "react"
import { cn } from "@/lib/utils"

const Badge = React.forwardRef(({ className, variant = "default", ...props }, ref) => {
  const variants = {
    default:     "bg-accent text-accent-fg",
    secondary:   "bg-surface-panel border border-surface-border text-text-default",
    destructive: "bg-red-600 text-white",
    outline:     "border border-surface-border text-text-default",
  }
  return (
    <span
      ref={ref}
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors",
        variants[variant],
        className
      )}
      {...props}
    />
  )
})
Badge.displayName = "Badge"

export { Badge }
