import { useState, useEffect } from 'react'

const ROTATING_MESSAGES = [
  'Reading your description',
  'Picking out the important parts',
  'Preparing your questions',
  'Almost ready',
]

const ESTIMATED_SECONDS = 25

export default function StructuringLoader() {
  const [elapsed, setElapsed] = useState(0)
  const [messageIdx, setMessageIdx] = useState(0)

  useEffect(() => {
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const t = setInterval(() => setMessageIdx((i) => (i + 1) % ROTATING_MESSAGES.length), 4000)
    return () => clearInterval(t)
  }, [])

  // Cap at 95% so the ring never looks finished before the backend responds
  const progress = Math.min(95, (elapsed / ESTIMATED_SECONDS) * 95)

  const remaining = Math.max(0, ESTIMATED_SECONDS - elapsed)
  const remainingLabel =
    remaining > 0
      ? `About ${remaining} second${remaining === 1 ? '' : 's'} left`
      : 'Almost done'

  const RADIUS = 54
  const CIRCUM = 2 * Math.PI * RADIUS
  const strokeDashoffset = CIRCUM - (progress / 100) * CIRCUM

  return (
    <div className="relative min-h-[420px] flex items-center justify-center animate-fade-in">
      {/* Ambient blobs */}
      <div className="absolute top-6 left-1/4 w-64 h-64 rounded-full
                      bg-accent/20 blur-3xl animate-pulse-slow pointer-events-none" />
      <div
        className="absolute bottom-6 right-1/4 w-64 h-64 rounded-full
                   bg-accent-secondary/20 blur-3xl animate-pulse-slow pointer-events-none"
        style={{ animationDelay: '2s' }}
      />

      <div className="relative z-10 flex flex-col items-center text-center max-w-md px-6">

        {/* Countdown ring */}
        <div className="relative w-36 h-36 mb-8">
          {/* SVG progress ring */}
          <svg className="absolute inset-0 -rotate-90" viewBox="0 0 120 120">
            <defs>
              <linearGradient id="ring-gradient" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%"   style={{ stopColor: 'rgb(var(--color-accent))' }} />
                <stop offset="100%" style={{ stopColor: 'rgb(var(--color-accent-secondary))' }} />
              </linearGradient>
            </defs>
            {/* Faint track */}
            <circle
              cx="60" cy="60" r={RADIUS}
              fill="none"
              strokeWidth="4"
              strokeOpacity="0.35"
              style={{ stroke: 'rgb(var(--color-surface-border))' }}
            />
            {/* Progress arc */}
            <circle
              cx="60" cy="60" r={RADIUS}
              fill="none"
              stroke="url(#ring-gradient)"
              strokeWidth="4"
              strokeLinecap="round"
              strokeDasharray={CIRCUM}
              strokeDashoffset={strokeDashoffset}
              style={{ transition: 'stroke-dashoffset 800ms ease-out' }}
            />
          </svg>

          {/* Orbiting particles */}
          <div className="absolute inset-0 animate-spin" style={{ animationDuration: '3.6s' }}>
            <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-0.5
                            w-2.5 h-2.5 rounded-full bg-accent shadow-lg shadow-accent/60" />
          </div>
          <div
            className="absolute inset-0 animate-spin"
            style={{ animationDuration: '5s', animationDirection: 'reverse' }}
          >
            <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-0.5
                            w-2 h-2 rounded-full bg-accent-secondary shadow-lg shadow-accent-secondary/60" />
          </div>

          {/* Center seconds counter */}
          <div className="absolute inset-6 rounded-full
                          bg-gradient-to-br from-accent to-accent-secondary
                          flex flex-col items-center justify-center
                          shadow-2xl shadow-accent/40">
            <span className="text-3xl font-black text-white leading-none tabular-nums">
              {remaining > 0 ? remaining : '…'}
            </span>
            {remaining > 0 && (
              <span className="text-[10px] text-white/80 font-bold uppercase tracking-widest mt-0.5">
                sec
              </span>
            )}
          </div>
        </div>

        {/* Heading — no "AI" mention */}
        <h2 className="text-2xl font-bold text-text-default mb-2">
          Getting things ready
        </h2>

        {/* Rotating status line */}
        <p key={messageIdx} className="text-text-muted text-base mb-2 animate-fade-in min-h-[24px]">
          {ROTATING_MESSAGES[messageIdx]}…
        </p>

        {/* Live time-remaining */}
        <p className="text-xs text-text-muted/70 mb-6 tabular-nums">
          {remainingLabel}
        </p>

        {/* Slim progress bar */}
        <div className="w-52 h-1.5 rounded-full bg-surface-border/60 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-secondary
                       transition-all duration-1000 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>
    </div>
  )
}
