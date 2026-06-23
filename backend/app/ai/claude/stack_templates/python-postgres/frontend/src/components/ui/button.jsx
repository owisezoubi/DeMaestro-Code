import * as React from "react"
import { cn } from "@/lib/utils"

const Button = React.forwardRef(({ className, variant = "default", size = "default", ...props }, ref) => {
  const variants = {
    default:     "bg-accent text-accent-fg hover:bg-accent/90",
    destructive: "bg-red-600 text-white hover:bg-red-700",
    outline:     "border border-surface-border bg-transparent hover:bg-surface-panel text-text-default",
    secondary:   "bg-surface-panel text-text-default hover:bg-surface-border border border-surface-border",
    ghost:       "hover:bg-surface-border text-text-default",
    link:        "underline text-accent",
  }
  const sizes = {
    default: "h-10 px-4 py-2",
    sm:      "h-8 px-3 text-sm",
    lg:      "h-11 px-6",
    icon:    "h-10 w-10",
  }
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    />
  )
})
Button.displayName = "Button"

export { Button }
