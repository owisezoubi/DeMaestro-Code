import { useState, useEffect } from 'react'
import { Sparkles, MessageCircle, Wand2, Brain, CheckCircle2 } from 'lucide-react'

const REFINE_STEPS = [
  { Icon: Brain,         label: 'Reading your answers'       },
  { Icon: MessageCircle, label: 'Understanding what you want' },
  { Icon: Wand2,         label: 'Refining the requirements'  },
  { Icon: Sparkles,      label: 'Almost there'               },
  { Icon: CheckCircle2,  label: 'Finalizing the plan'        },
]

const INIT_STEPS = [
  { Icon: Brain,         label: 'Reading your description' },
  { Icon: MessageCircle, label: 'Preparing your questions' },
  { Icon: Sparkles,      label: 'Almost ready'             },
]

export default function ClarificationLoader({ mode = 'refine' }) {
  const steps = mode === 'refine' ? REFINE_STEPS : INIT_STEPS
  const [stepIdx, setStepIdx] = useState(0)

  useEffect(() => {
    const t = setInterval(() => setStepIdx((i) => (i + 1) % steps.length), 1600)
    return () => clearInterval(t)
  }, [steps.length])

  const { Icon } = steps[stepIdx]

  return (
    <div className="relative min-h-[380px] flex items-center justify-center animate-fade-in">
      {/* Ambient blobs */}
      <div className="absolute top-8 left-1/4 w-56 h-56 rounded-full
                      bg-accent/20 blur-3xl animate-pulse-slow pointer-events-none" />
      <div
        className="absolute bottom-4 right-1/4 w-56 h-56 rounded-full
                   bg-accent-secondary/20 blur-3xl animate-pulse-slow pointer-events-none"
        style={{ animationDelay: '2s' }}
      />

      <div className="relative z-10 flex flex-col items-center text-center max-w-md px-6">
        {/* Orbital ring */}
        <div className="relative w-28 h-28 mb-7">
          {/* Outer conic ring */}
          <div
            className="absolute inset-0 rounded-full animate-spin"
            style={{
              animationDuration: '2.4s',
              background: 'conic-gradient(from 0deg, transparent 0%, rgb(99 102 241 / 0.7) 20%, transparent 40%)',
            }}
          />
          {/* Inner mask */}
          <div className="absolute inset-2 rounded-full bg-surface-page" />

          {/* Particle 1 — clockwise */}
          <div className="absolute inset-0 animate-spin" style={{ animationDuration: '3.6s' }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2
                            w-2.5 h-2.5 rounded-full bg-accent shadow-lg shadow-accent/50" />
          </div>
          {/* Particle 2 — counter-clockwise */}
          <div
            className="absolute inset-0 animate-spin"
            style={{ animationDuration: '4.8s', animationDirection: 'reverse' }}
          >
            <div className="absolute bottom-0 left-1/2 -translate-x-1/2
                            w-2 h-2 rounded-full bg-accent-secondary shadow-lg shadow-accent-secondary/50" />
          </div>

          {/* Center gradient with rotating icon */}
          <div className="absolute inset-4 rounded-full
                          bg-gradient-to-br from-accent to-accent-secondary
                          flex items-center justify-center
                          shadow-2xl shadow-accent/40">
            <Icon key={stepIdx} className="w-8 h-8 text-white animate-fade-in" />
          </div>
        </div>

        {/* Heading */}
        <h2 className="text-xl font-bold text-text-default mb-1.5">
          {mode === 'refine' ? 'Refining your requirements' : 'Getting your questions ready'}
        </h2>

        {/* Rotating step label */}
        <p key={stepIdx} className="text-text-muted text-sm mb-5 animate-fade-in min-h-[20px]">
          {steps[stepIdx].label}…
        </p>

        {/* Progress dots */}
        <div className="flex items-center gap-2">
          {steps.map((_, i) => (
            <div
              key={i}
              className={`h-1.5 rounded-full transition-all duration-500
                         ${i === stepIdx
                           ? 'w-8 bg-accent'
                           : i < stepIdx
                             ? 'w-1.5 bg-accent/70'
                             : 'w-1.5 bg-surface-border'}`}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
