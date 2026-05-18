import { cn } from "@/lib/utils"

export function Button({ className, variant = "default", size = "default", ...props }) {
  const variants = {
    default: "bg-slate-900 text-white hover:bg-slate-800",
    destructive: "bg-red-600 text-white hover:bg-red-700",
    outline: "border border-slate-300 bg-white hover:bg-slate-50",
    secondary: "bg-slate-100 text-slate-900 hover:bg-slate-200",
    ghost: "hover:bg-slate-100",
    link: "underline text-blue-600",
  }
  const sizes = { default: "h-10 px-4 py-2", sm: "h-8 px-3 text-sm", lg: "h-11 px-6", icon: "h-10 w-10" }
  return <button className={cn("inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:opacity-50", variants[variant], sizes[size], className)} {...props} />
}
