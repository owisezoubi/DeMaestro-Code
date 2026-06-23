import * as React from "react"
import { cn } from "@/lib/utils"

const Textarea = React.forwardRef(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "flex min-h-[80px] w-full rounded-md border border-surface-border bg-surface-panel px-3 py-2 text-sm text-text-default placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-50 resize-none",
      className
    )}
    {...props}
  />
))
Textarea.displayName = "Textarea"

export { Textarea }
