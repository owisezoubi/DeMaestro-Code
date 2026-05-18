import { cn } from "@/lib/utils"
import { useState } from "react"

export function TooltipProvider({ children }) { return <>{children}</> }

export function Tooltip({ children }) { return <>{children}</> }

export function TooltipTrigger({ asChild, children, ...props }) {
  return asChild ? children : <span {...props}>{children}</span>
}

export function TooltipContent({ className, children, ...props }) {
  return (
    <div
      className={cn("z-50 overflow-hidden rounded-md bg-slate-900 px-3 py-1.5 text-xs text-white shadow-md", className)}
      {...props}
    >
      {children}
    </div>
  )
}
