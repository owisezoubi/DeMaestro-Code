import { Sparkles, Code, Layers, Smartphone, Zap, Package } from 'lucide-react'

function FloatingCard({ delay = 0, duration = 8, x = 0, y = 0, rotate = 0, children, className = '' }) {
  return (
    <div
      className={`absolute animate-float ${className}`}
      style={{
        left: `${x}%`,
        top: `${y}%`,
        animationDelay: `${delay}s`,
        animationDuration: `${duration}s`,
        '--rotate': `${rotate}deg`,
      }}
    >
      {children}
    </div>
  )
}

function CodeWindow() {
  return (
    <div className="w-56 rounded-xl bg-zinc-900/90 backdrop-blur shadow-2xl
                    border border-white/10 overflow-hidden">
      <div className="flex items-center gap-1.5 px-3 py-2 bg-white/5">
        <span className="w-2 h-2 rounded-full bg-red-400" />
        <span className="w-2 h-2 rounded-full bg-yellow-400" />
        <span className="w-2 h-2 rounded-full bg-green-400" />
      </div>
      <div className="p-3 text-[10px] font-mono leading-relaxed">
        <div className="text-purple-300">{'function '}<span className="text-blue-300">App</span>{'() {'}</div>
        <div className="text-zinc-400 pl-3">{'return ('}</div>
        <div className="text-pink-300 pl-6">{'<Dashboard />'}</div>
        <div className="text-zinc-400 pl-3">{');'}</div>
        <div className="text-purple-300">{'}'}</div>
      </div>
    </div>
  )
}

function PhoneMockup() {
  return (
    <div className="w-32 h-56 rounded-3xl bg-zinc-900/90 backdrop-blur
                    shadow-2xl border-2 border-white/10 p-2">
      <div className="w-full h-full rounded-2xl
                      bg-gradient-to-br from-indigo-500 to-purple-600
                      p-3 flex flex-col gap-2">
        <div className="h-2 w-12 rounded bg-white/40" />
        <div className="h-1 w-20 rounded bg-white/20" />
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <div className="h-8 rounded bg-white/20" />
          <div className="h-8 rounded bg-white/20" />
          <div className="h-8 rounded bg-white/20" />
          <div className="h-8 rounded bg-white/30" />
        </div>
      </div>
    </div>
  )
}

function IconBubble({ Icon, color }) {
  return (
    <div className={`w-14 h-14 rounded-2xl backdrop-blur shadow-xl
                     border border-white/20 flex items-center justify-center ${color}`}>
      <Icon className="w-6 h-6 text-white" />
    </div>
  )
}

export default function AuthScene() {
  return (
    <div className="hidden lg:block flex-1 relative overflow-hidden
                    bg-gradient-to-br from-indigo-600 via-violet-600 to-fuchsia-700">
      {/* Background glow blobs */}
      <div className="absolute inset-0
                      bg-[radial-gradient(circle_at_30%_20%,rgba(255,255,255,0.2),transparent_50%)]" />
      <div className="absolute inset-0
                      bg-[radial-gradient(circle_at_70%_80%,rgba(255,255,255,0.12),transparent_50%)]" />

      {/* Floating UI primitives */}
      <FloatingCard x={10} y={15} rotate={-8} delay={0} duration={9}>
        <CodeWindow />
      </FloatingCard>

      <FloatingCard x={55} y={20} rotate={6} delay={1.5} duration={10}>
        <PhoneMockup />
      </FloatingCard>

      <FloatingCard x={20} y={65} rotate={4} delay={2} duration={8}>
        <IconBubble Icon={Sparkles} color="bg-yellow-500/80" />
      </FloatingCard>

      <FloatingCard x={70} y={55} rotate={-6} delay={0.5} duration={11}>
        <IconBubble Icon={Code} color="bg-emerald-500/80" />
      </FloatingCard>

      <FloatingCard x={40} y={75} rotate={3} delay={3} duration={9}>
        <IconBubble Icon={Layers} color="bg-pink-500/80" />
      </FloatingCard>

      <FloatingCard x={75} y={30} rotate={-4} delay={2.5} duration={10}>
        <IconBubble Icon={Zap} color="bg-amber-500/80" />
      </FloatingCard>

      <FloatingCard x={5} y={45} rotate={5} delay={1} duration={11}>
        <IconBubble Icon={Package} color="bg-sky-500/80" />
      </FloatingCard>

      <FloatingCard x={50} y={50} rotate={-3} delay={4} duration={12}>
        <IconBubble Icon={Smartphone} color="bg-rose-500/80" />
      </FloatingCard>

      {/* Particle dots (deterministic positions) */}
      {Array.from({ length: 30 }, (_, i) => (
        <div
          key={i}
          className="absolute w-1 h-1 rounded-full bg-white/60 animate-twinkle"
          style={{
            top: `${(i * 31 + 7) % 100}%`,
            left: `${(i * 47 + 17) % 100}%`,
            animationDelay: `${(i * 0.1) % 3}s`,
            animationDuration: `${2 + (i * 0.1) % 3}s`,
          }}
        />
      ))}

      {/* Centered tagline */}
      <div className="relative z-10 flex flex-col items-center justify-center
                      h-full text-white px-12 text-center pointer-events-none">
        <h2 className="text-4xl xl:text-5xl font-black mb-4 leading-tight drop-shadow-2xl">
          From idea<br />to live app
        </h2>
        <p className="text-lg text-white/80 max-w-md drop-shadow">
          Describe what you want. We generate, test, and deploy it for you.
        </p>
      </div>
    </div>
  )
}
