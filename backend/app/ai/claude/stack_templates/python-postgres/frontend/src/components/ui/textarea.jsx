import { cn } from "@/lib/utils"

export function Textarea({ className, ...props }) {
  return <textarea className={cn("flex min-h-[80px] w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50 resize-none", className)} {...props} />
}
