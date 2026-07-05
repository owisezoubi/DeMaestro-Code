import { useState, useEffect, useRef } from 'react'
import { ChevronLeft, ChevronRight, Check, Quote } from 'lucide-react'

export default function ExampleShowcase({ example, index }) {
  const shots = example.screenshots || []
  const [current, setCurrent] = useState(0)
  const [paused, setPaused] = useState(false)
  const [inView, setInView] = useState(false)
  const rootRef = useRef(null)
  const timerRef = useRef(null)

  const reversed = index % 2 === 1

  // Auto-advance every 4s when in view and not paused
  useEffect(() => {
    if (paused || !inView || shots.length <= 1) return
    timerRef.current = setInterval(() => {
      setCurrent((c) => (c + 1) % shots.length)
    }, 4000)
    return () => clearInterval(timerRef.current)
  }, [paused, inView, shots.length])

  // Scroll-reveal via IntersectionObserver
  useEffect(() => {
    const el = rootRef.current
    if (!el) return
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true)
          io.disconnect()
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -80px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  const goPrev = (e) => {
    e.stopPropagation()
    setCurrent((c) => (c - 1 + shots.length) % shots.length)
  }
  const goNext = (e) => {
    e.stopPropagation()
    setCurrent((c) => (c + 1) % shots.length)
  }

  return (
    <div
      ref={rootRef}
      className={`grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-16 items-center
                 transition-all duration-1000
                 ${inView ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'}`}
    >
      {/* ─── Screenshot carousel ─── */}
      <div
        className={`relative ${reversed ? 'lg:order-2' : 'lg:order-1'}`}
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
      >
        {/* Ambient glow */}
        <div
          className={`absolute -inset-8 rounded-3xl bg-gradient-to-br ${example.accent}
                     opacity-20 blur-3xl pointer-events-none`}
        />

        {/* Browser frame */}
        <div className={`relative rounded-3xl overflow-hidden
                        border border-surface-border bg-surface-panel
                        shadow-2xl ${example.accentGlow}`}>

          {/* Faux browser chrome */}
          <div className="relative h-9 flex items-center gap-2 px-4
                          border-b border-surface-border bg-surface-page">
            <div className="flex gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-400/70" />
              <div className="w-2.5 h-2.5 rounded-full bg-amber-400/70" />
              <div className="w-2.5 h-2.5 rounded-full bg-emerald-400/70" />
            </div>
            <div className="flex-1 max-w-md ml-4 h-5 rounded bg-surface-border/60
                            flex items-center px-2">
              <span className="text-[10px] text-text-muted font-mono truncate">
                {example.title.toLowerCase().replace(/\s+/g, '')}.app
              </span>
            </div>
          </div>

          {/* Screenshot area */}
          <div className={`relative aspect-[16/10] bg-gradient-to-br ${example.accent} overflow-hidden`}>

            {shots.length === 0 ? (
              <div className="absolute inset-0 flex items-center justify-center
                             text-white/70 text-sm">
                Screenshots coming soon
              </div>
            ) : (
              <>
                {shots.map((src, i) => (
                  <img
                    key={src}
                    src={src}
                    alt={`${example.title} — screen ${i + 1}`}
                    className={`absolute inset-0 w-full h-full object-cover object-top
                               transition-all duration-700
                               ${i === current
                                 ? 'opacity-100 scale-100'
                                 : 'opacity-0 scale-[1.03]'}`}
                    loading="lazy"
                    onError={(e) => { e.target.style.display = 'none' }}
                  />
                ))}

                {shots.length > 1 && (
                  <>
                    <button
                      onClick={goPrev}
                      aria-label="Previous screen"
                      className="absolute left-3 top-1/2 -translate-y-1/2 z-20
                                 w-10 h-10 rounded-full bg-black/40 backdrop-blur
                                 text-white flex items-center justify-center
                                 hover:bg-black/60 transition-all duration-200"
                      style={{ opacity: paused ? 1 : 0 }}
                    >
                      <ChevronLeft className="w-5 h-5" />
                    </button>
                    <button
                      onClick={goNext}
                      aria-label="Next screen"
                      className="absolute right-3 top-1/2 -translate-y-1/2 z-20
                                 w-10 h-10 rounded-full bg-black/40 backdrop-blur
                                 text-white flex items-center justify-center
                                 hover:bg-black/60 transition-all duration-200"
                      style={{ opacity: paused ? 1 : 0 }}
                    >
                      <ChevronRight className="w-5 h-5" />
                    </button>

                    {/* Screen counter */}
                    <div className="absolute top-3 right-3 z-20
                                    px-2.5 py-1 rounded-full
                                    bg-black/40 backdrop-blur
                                    text-white text-[10px] font-mono">
                      {current + 1} / {shots.length}
                    </div>

                    {/* Progress dots */}
                    <div className="absolute bottom-4 inset-x-0 z-20
                                    flex items-center justify-center gap-2">
                      {shots.map((_, i) => (
                        <button
                          key={i}
                          onClick={(e) => { e.stopPropagation(); setCurrent(i) }}
                          aria-label={`Screen ${i + 1}`}
                          className={`h-1.5 rounded-full transition-all duration-500
                                     ${i === current
                                       ? 'w-8 bg-white'
                                       : 'w-1.5 bg-white/50 hover:bg-white/70'}`}
                        />
                      ))}
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* ─── Description side ─── */}
      <div className={`${reversed ? 'lg:order-1' : 'lg:order-2'} space-y-6 text-left`}>

        {/* Tag pills */}
        <div className="flex flex-wrap gap-2">
          {example.tags.map((t) => (
            <span
              key={t}
              className="inline-flex items-center px-2.5 py-1 rounded-full
                         bg-accent/10 border border-accent/20
                         text-[10px] font-bold text-accent uppercase tracking-widest"
            >
              {t}
            </span>
          ))}
        </div>

        {/* Title + tagline */}
        <div>
          <h3 className="text-4xl md:text-5xl font-black text-text-default mb-3
                         leading-tight tracking-tight">
            {example.title}
          </h3>
          <p className="text-lg text-text-muted italic leading-relaxed">
            {example.tagline}
          </p>
        </div>

        {/* Prompt quote */}
        <div className="relative pl-5 py-1 border-l-2 border-accent/40">
          <Quote className="absolute -left-2.5 -top-0.5 w-4 h-4 text-accent
                            bg-surface-page rounded-full p-0.5" />
          <p className="text-sm text-text-default font-medium leading-relaxed">
            {example.prompt}
          </p>
          <p className="text-xs text-text-muted mt-1">
            The one-sentence prompt that built this app.
          </p>
        </div>

        {/* Summary */}
        <p className="text-base text-text-default/85 leading-relaxed">
          {example.summary}
        </p>

        {/* Features */}
        <div>
          <p className="text-xs font-bold text-accent uppercase tracking-widest mb-3">
            What DeMaestro built
          </p>
          <ul className="space-y-2">
            {example.features.map((f) => (
              <li key={f} className="flex items-start gap-2.5 text-sm text-text-default">
                <span className="mt-0.5 flex-shrink-0 w-4 h-4 rounded-full
                                 bg-gradient-to-br from-accent to-accent-secondary
                                 flex items-center justify-center">
                  <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                </span>
                {f}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
