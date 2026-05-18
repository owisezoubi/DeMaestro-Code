import { cn } from "@/lib/utils"

export function Avatar({ className, ...props }) {
  return <span className={cn("relative flex h-10 w-10 shrink-0 overflow-hidden rounded-full", className)} {...props} />
}
export function AvatarImage({ className, src, alt = "", ...props }) {
  return <img src={src} alt={alt} className={cn("aspect-square h-full w-full object-cover", className)} {...props} />
}
export function AvatarFallback({ className, ...props }) {
  return <span className={cn("flex h-full w-full items-center justify-center rounded-full bg-slate-100 text-sm font-medium text-slate-600", className)} {...props} />
}
