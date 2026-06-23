import * as React from "react"
import { cn } from "@/lib/utils"

/* TooltipProvider and Tooltip are pass-through wrappers — no DOM node to ref. */
export function TooltipProvider({ children }) { return <>{children}</> }

export function Tooltip({ children }) { return <>{children}</> }

const TooltipTrigger = React.forwardRef(({ asChild, children, ...props }, ref) => {
  if (asChild) return children
  return <span ref={ref} {...props}>{children}</span>
})
TooltipTrigger.displayName = "TooltipTrigger"

const TooltipContent = React.forwardRef(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "z-50 overflow-hidden rounded-md bg-text-default px-3 py-1.5 text-xs text-surface-page shadow-md",
      className
    )}
    {...props}
  >
    {children}
  </div>
))
TooltipContent.displayName = "TooltipContent"

export { TooltipTrigger, TooltipContent }
