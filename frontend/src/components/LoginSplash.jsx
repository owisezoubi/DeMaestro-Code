import Logo from '@/components/Logo'

export default function LoginSplash({ onDone }) {
  return (
    <div
      className="fixed inset-0 z-[100] flex flex-col items-center
                 justify-center splash-fade-out
                 bg-gradient-to-br from-surface-page via-accent/10 to-accent-secondary/15
                 backdrop-blur-md"
      onAnimationEnd={onDone}
    >
      {/* Radial glow */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(99,102,241,0.15),transparent_60%)]" />

      {/* Floating particles */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        {[...Array(20)].map((_, i) => (
          <div
            key={i}
            className="absolute w-1 h-1 rounded-full bg-accent/40 animate-ping"
            style={{
              top: `${(i * 37 + 7) % 100}%`,
              left: `${(i * 53 + 13) % 100}%`,
              animationDelay: `${(i * 0.13) % 2}s`,
              animationDuration: `${2 + (i * 0.11) % 2}s`,
            }}
          />
        ))}
      </div>

      <div className="relative">
        <Logo size={120} className="logo-launch" />
      </div>

      <p className="relative mt-8 text-text-default font-medium tracking-wide animate-pulse">
        Welcome back
      </p>
    </div>
  )
}
