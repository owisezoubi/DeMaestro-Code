import { cn } from "@/lib/utils"

export function Alert({ className, variant = "default", ...props }) {
  const variants = {
    default: "bg-white border-slate-200 text-slate-900",
    destructive: "bg-red-50 border-red-200 text-red-800",
  }
  return <div role="alert" className={cn("relative w-full rounded-lg border p-4", variants[variant], className)} {...props} />
}
export function AlertTitle({ className, ...props }) {
  return <h5 className={cn("mb-1 font-semibold leading-none tracking-tight", className)} {...props} />
}
export function AlertDescription({ className, ...props }) {
  return <div className={cn("text-sm", className)} {...props} />
}
