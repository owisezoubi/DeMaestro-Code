import * as React from "react"
import { cn } from "@/lib/utils"

const Input = React.forwardRef(({ className, type, ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(
      "flex h-10 w-full rounded-md border border-surface-border bg-surface-panel px-3 py-2 text-sm text-text-default placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-50",
      className
    )}
    {...props}
  />
))
Input.displayName = "Input"

export { Input }
