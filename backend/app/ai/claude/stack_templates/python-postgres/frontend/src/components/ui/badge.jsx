import { cn } from "@/lib/utils"

export function Badge({ className, variant = "default", ...props }) {
  const variants = {
    default: "bg-slate-900 text-white",
    secondary: "bg-slate-100 text-slate-900",
    destructive: "bg-red-600 text-white",
    outline: "border border-slate-300 text-slate-700",
  }
  return <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors", variants[variant], className)} {...props} />
}
