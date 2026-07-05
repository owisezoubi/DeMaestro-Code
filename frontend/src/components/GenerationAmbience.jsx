import { Code2, Braces, Cpu, Database, Cog, Sparkles, GitBranch, FileCode } from 'lucide-react'

const LEFT_ICONS = [Code2, Braces, Cpu, Database]
const RIGHT_ICONS = [Cog, Sparkles, GitBranch, FileCode]

const ICON_POSITIONS = [
  { top: '12%', delay: '0s' },
  { top: '28%', delay: '1.2s' },
  { top: '50%', delay: '2.4s' },
  { top: '70%', delay: '0.6s' },
]

export default function GenerationAmbience() {
  return (
    <div
      className="fixed inset-0 -z-10 pointer-events-none overflow-hidden"
      aria-hidden="true"
    >
      {/* Dotted grid overlay */}
      <div
        className="absolute inset-0 opacity-[0.05] text-text-default"
        style={{
          backgroundImage: 'radial-gradient(circle, currentColor 1px, transparent 1px)',
          backgroundSize: '24px 24px',
        }}
      />

      {/* Left orb */}
      <div
        className="absolute -left-32 top-1/4 w-[480px] h-[480px] rounded-full
                   bg-gradient-to-br from-accent/15 to-accent-secondary/10
                   blur-3xl animate-pulse-slow"
      />

      {/* Right orb */}
      <div
        className="absolute -right-32 top-1/3 w-[480px] h-[480px] rounded-full
                   bg-gradient-to-br from-accent-secondary/15 to-accent/10
                   blur-3xl animate-pulse-slow"
        style={{ animationDelay: '3s' }}
      />

      {/* Left icon ring */}
      <div className="absolute left-6 inset-y-0 flex flex-col justify-around py-16">
        {LEFT_ICONS.map((Icon, i) => (
          <div
            key={i}
            className="opacity-[0.08] dark:opacity-[0.12] text-accent animate-float-y"
            style={{ animationDelay: ICON_POSITIONS[i]?.delay || '0s' }}
          >
            <Icon className="w-8 h-8" />
          </div>
        ))}
      </div>

      {/* Right icon ring */}
      <div className="absolute right-6 inset-y-0 flex flex-col justify-around py-16">
        {RIGHT_ICONS.map((Icon, i) => (
          <div
            key={i}
            className="opacity-[0.08] dark:opacity-[0.12] text-accent animate-float-y-delayed"
            style={{ animationDelay: ICON_POSITIONS[i]?.delay || '0s' }}
          >
            <Icon className="w-8 h-8" />
          </div>
        ))}
      </div>
    </div>
  )
}
