import { useState, useEffect } from 'react'
import { Sparkles, Code2, Braces, Cpu, Rocket } from 'lucide-react'

const STEPS = [
  { Icon: Sparkles, label: 'Getting things ready' },
  { Icon: Code2,    label: 'Designing the architecture…' },
  { Icon: Braces,   label: 'Writing the initial code…' },
  { Icon: Cpu,      label: 'Testing and verifying…' },
  { Icon: Rocket,   label: 'Packaging everything up…' },
]

const DOT_COUNT = 5

export default function CreatingProjectLoader() {
  const [stepIdx, setStepIdx] = useState(0)
  const [dotIdx, setDotIdx]   = useState(0)

  useEffect(() => {
    const t = setInterval(() => setStepIdx((i) => (i + 1) % STEPS.length), 1600)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const t = setInterval(() => setDotIdx((i) => (i + 1) % DOT_COUNT), 500)
    return () => clearInterval(t)
  }, [])

  const { Icon } = STEPS[stepIdx]

  return (
    <div className="relative flex flex-col items-center justify-center py-16 min-h-[380px]">
      {/* Ambient blobs */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 -left-20 w-64 h-64 rounded-full
                        bg-accent/10 blur-3xl animate-pulse-slow" />
        <div
          className="absolute bottom-1/4 -right-20 w-64 h-64 rounded-full
                     bg-accent-secondary/10 blur-3xl animate-pulse-slow"
          style={{ animationDelay: '2s' }}
        />
      </div>

      {/* Orbital rig */}
      <div className="relative mb-10 flex-shrink-0" style={{ width: 160, height: 160 }}>
        {/* Outer conic ring — spins */}
        <div
          className="absolute inset-0 rounded-full animate-spin"
          style={{
            animationDuration: '2.4s',
            background: 'conic-gradient(from 0deg, transparent 0%, rgb(99 102 241 / 0.7) 20%, transparent 40%)',
          }}
        />

        {/* Particle 1 — clockwise */}
        <div
          className="absolute inset-0 animate-spin"
          style={{ animationDuration: '3.6s' }}
        >
          <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1.5
                          w-3 h-3 rounded-full bg-accent shadow-lg shadow-accent/60" />
        </div>

        {/* Particle 2 — counter-clockwise */}
        <div
          className="absolute inset-0 animate-spin [animation-direction:reverse]"
          style={{ animationDuration: '4.8s' }}
        >
          <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-1.5
                          w-2 h-2 rounded-full bg-accent-secondary shadow-md shadow-accent-secondary/50" />
        </div>

        {/* Center gradient circle */}
        <div className="absolute inset-[20px] rounded-full
                        bg-gradient-to-br from-accent to-accent-secondary
                        flex items-center justify-center
                        shadow-xl shadow-accent/30">
          <Icon key={stepIdx} className="w-8 h-8 text-white animate-fade-in" />
        </div>
      </div>

      {/* Heading */}
      <h2 className="text-2xl font-bold text-text-default mb-2">
        Creating your new project
      </h2>

      {/* Rotating step label */}
      <p key={stepIdx} className="text-sm text-text-muted animate-fade-in mb-8 h-5">
        {STEPS[stepIdx].label}
      </p>

      {/* Progress dots */}
      <div className="flex items-center gap-2">
        {Array.from({ length: DOT_COUNT }).map((_, i) => (
          <div
            key={i}
            className={`h-2 rounded-full bg-accent transition-all duration-300
                        ${i === dotIdx ? 'w-6 opacity-100' : 'w-2 opacity-30'}`}
          />
        ))}
      </div>
    </div>
  )
}
