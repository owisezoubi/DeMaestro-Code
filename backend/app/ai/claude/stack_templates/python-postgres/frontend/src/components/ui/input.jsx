import { cn } from "@/lib/utils"

export function Input({ className, ...props }) {
  return <input className={cn("flex h-10 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50", className)} {...props} />
}
