import * as React from "react"
import { cn } from "@/lib/utils"

const Skeleton = React.forwardRef(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("animate-pulse rounded-md bg-surface-border", className)}
    {...props}
  />
))
Skeleton.displayName = "Skeleton"

export { Skeleton }
