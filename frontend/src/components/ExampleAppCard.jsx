import { useState, useEffect, useRef } from 'react'
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react'

// CSS-drawn fallback previews (used when no screenshots are available yet)
const PREVIEW_LAYOUTS = {
  todo: () => (
    <div className="space-y-2">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-4 h-4 rounded border-2 border-white/60" />
        <div className="h-2.5 w-2/3 rounded bg-white/70" />
      </div>
      {[85, 60, 78, 45].map((w, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="w-3 h-3 rounded border border-white/50" />
          <div className="h-2 rounded bg-white/40" style={{ width: `${w}%` }} />
        </div>
      ))}
    </div>
  ),
  portfolio: () => (
    <div className="space-y-2">
      <div className="h-2 w-1/3 rounded bg-white/80 mb-2" />
      <div className="grid grid-cols-3 gap-1.5">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="aspect-square rounded bg-white/25" />
        ))}
      </div>
    </div>
  ),
  recipes: () => (
    <div className="space-y-2">
      <div className="h-8 rounded-lg bg-white/25 flex items-center px-2">
        <div className="h-1.5 w-1/2 rounded bg-white/60" />
      </div>
      {[70, 55, 85].map((w, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-white/30" />
          <div className="flex-1 space-y-1">
            <div className="h-1.5 rounded bg-white/60" style={{ width: `${w}%` }} />
            <div className="h-1 rounded bg-white/30 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  ),
  notes: () => (
    <div className="space-y-2">
      {[95, 82, 70, 55, 40].map((w, i) => (
        <div key={i} className="h-1.5 rounded bg-white/50" style={{ width: `${w}%` }} />
      ))}
      <div className="flex gap-1 mt-3">
        {['#work', '#idea', '#focus'].map((t) => (
          <div key={t} className="h-4 px-1.5 rounded-full bg-white/30 text-[8px] flex items-center text-white/90 font-mono">
            {t}
          </div>
        ))}
      </div>
    </div>
  ),
  events: () => (
    <div className="space-y-2">
      <div className="grid grid-cols-7 gap-0.5 mb-2">
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="h-2 rounded bg-white/40" />
        ))}
      </div>
      {[1, 2, 3].map((row) => (
        <div key={row} className="flex items-center gap-2 p-1 rounded bg-white/20">
          <div className="w-2 h-2 rounded-full bg-white" />
          <div className="h-1.5 flex-1 rounded bg-white/50" />
        </div>
      ))}
    </div>
  ),
  library: () => (
    <div className="grid grid-cols-4 gap-1.5">
      {['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((k) => (
        <div key={k} className="aspect-[3/4] rounded bg-white/25 flex items-end p-1">
          <div className="h-0.5 w-full rounded bg-white/60" />
        </div>
      ))}
    </div>
  ),
}

function FallbackPreview({ kind }) {
  const Layout = PREVIEW_LAYOUTS[kind] || PREVIEW_LAYOUTS.todo
  return <Layout />
}

export default function ExampleAppCard({ example, index }) {
  const shots = (example.screenshots || [])
  // Track which images have actually loaded; hide 404s
  const [loadedShots, setLoadedShots] = useState([])
  const [current, setCurrent] = useState(0)
  const [paused, setPaused] = useState(false)
  const timerRef = useRef(null)

  const handleLoad = (src) => setLoadedShots((prev) => [...prev, src])
  const handleError = (src) => {
    // Remove errored image from loaded set (keeps it hidden)
    setLoadedShots((prev) => prev.filter((s) => s !== src))
  }

  const visibleShots = shots.filter((s) => loadedShots.includes(s))
  const hasScreenshots = visibleShots.length > 0

  // Auto-advance every 3.5s unless hovered
  useEffect(() => {
    if (paused || visibleShots.length <= 1) return
    timerRef.current = setInterval(() => {
      setCurrent((c) => (c + 1) % visibleShots.length)
    }, 3500)
    return () => clearInterval(timerRef.current)
  }, [paused, visibleShots.length])

  // Reset index if it goes out of bounds after an image error
  useEffect(() => {
    if (current >= visibleShots.length && visibleShots.length > 0) {
      setCurrent(0)
    }
  }, [current, visibleShots.length])

  const goPrev = (e) => {
    e.stopPropagation()
    setCurrent((c) => (c - 1 + visibleShots.length) % visibleShots.length)
  }
  const goNext = (e) => {
    e.stopPropagation()
    setCurrent((c) => (c + 1) % visibleShots.length)
  }

  return (
    <div
      className="group relative rounded-2xl overflow-hidden
                 bg-surface-panel border border-surface-border
                 hover:-translate-y-1 hover:shadow-2xl hover:shadow-accent/25
                 hover:border-accent/50
                 transition-all duration-300
                 animate-fade-in"
      style={{ animationDelay: `${index * 100}ms` }}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {/* Hero area — real screenshots OR CSS fallback */}
      <div className={`relative aspect-[16/10] bg-gradient-to-br ${example.accent} overflow-hidden`}>

        {/* Faux browser chrome */}
        <div className="absolute top-0 inset-x-0 h-6 bg-black/20
                        backdrop-blur-sm flex items-center px-3 gap-1 z-10">
          <div className="w-2 h-2 rounded-full bg-white/50" />
          <div className="w-2 h-2 rounded-full bg-white/50" />
          <div className="w-2 h-2 rounded-full bg-white/50" />
          <div className="ml-3 h-3 flex-1 max-w-[200px] rounded bg-white/20" />
        </div>

        {/* Hidden preload imgs — all shots, even before they're "visible" */}
        {shots.map((src) => (
          <img
            key={src}
            src={src}
            alt=""
            className="sr-only"
            onLoad={() => handleLoad(src)}
            onError={() => handleError(src)}
            loading="lazy"
          />
        ))}

        {hasScreenshots ? (
          <>
            {/* Slide images */}
            {visibleShots.map((src, i) => (
              <img
                key={src}
                src={src}
                alt={`${example.title} — slide ${i + 1}`}
                className={`absolute inset-0 w-full h-full object-cover object-top pt-6
                           transition-all duration-700
                           ${i === current ? 'opacity-100 scale-100' : 'opacity-0 scale-105'}`}
              />
            ))}

            {/* Prev/Next arrows — visible on hover */}
            {visibleShots.length > 1 && (
              <>
                <button
                  onClick={goPrev}
                  aria-label="Previous screenshot"
                  className="absolute left-2 top-1/2 -translate-y-1/2 z-20
                             w-8 h-8 rounded-full bg-black/40 backdrop-blur
                             text-white opacity-0 group-hover:opacity-100
                             hover:bg-black/60 transition-all
                             flex items-center justify-center"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  onClick={goNext}
                  aria-label="Next screenshot"
                  className="absolute right-2 top-1/2 -translate-y-1/2 z-20
                             w-8 h-8 rounded-full bg-black/40 backdrop-blur
                             text-white opacity-0 group-hover:opacity-100
                             hover:bg-black/60 transition-all
                             flex items-center justify-center"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </>
            )}

            {/* Dot indicators */}
            {visibleShots.length > 1 && (
              <div className="absolute bottom-2 inset-x-0 z-20 flex items-center justify-center gap-1.5">
                {visibleShots.map((_, i) => (
                  <button
                    key={i}
                    onClick={(e) => { e.stopPropagation(); setCurrent(i) }}
                    aria-label={`Go to slide ${i + 1}`}
                    className={`h-1.5 rounded-full transition-all duration-300
                               ${i === current
                                 ? 'w-6 bg-white'
                                 : 'w-1.5 bg-white/50 hover:bg-white/70'}`}
                  />
                ))}
              </div>
            )}
          </>
        ) : (
          /* CSS-drawn fallback when no screenshots have loaded yet */
          <div className="absolute inset-0 pt-6 p-5 text-white">
            <FallbackPreview kind={example.preview || 'todo'} />
            {/* Corner shine */}
            <div className="absolute -top-6 -right-6 w-24 h-24 rounded-full bg-white/20 blur-2xl pointer-events-none" />
          </div>
        )}
      </div>

      {/* Meta */}
      <div className="p-5">
        <div className="flex items-start justify-between gap-2 mb-2">
          <h3 className="text-lg font-bold text-text-default">{example.title}</h3>
          <div className="flex gap-1 flex-shrink-0">
            {example.tags.map((t) => (
              <span
                key={t}
                className="text-[10px] font-medium px-2 py-0.5 rounded-full
                           bg-accent/10 text-accent uppercase tracking-wider"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
        <p className="text-sm text-text-muted italic leading-relaxed mb-3">
          {example.prompt}
        </p>

        {example.liveUrl && (
          <a
            href={example.liveUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-xs font-medium
                       text-accent hover:text-accent-secondary transition-colors"
          >
            View live demo
            <ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>

      {/* Hover shine */}
      <div className="absolute inset-0 pointer-events-none
                      bg-gradient-to-tr from-transparent via-accent/5 to-transparent
                      opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
    </div>
  )
}
