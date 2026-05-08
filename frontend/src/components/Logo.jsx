import { Sparkles } from 'lucide-react'

export default function Logo({ className = '' }) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <Sparkles className="w-7 h-7 text-primary-600" />
      <span className="text-2xl font-bold text-primary-700 tracking-tight">
        DeMaestro
      </span>
    </div>
  )
}
