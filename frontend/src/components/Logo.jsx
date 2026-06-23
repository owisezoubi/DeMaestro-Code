import logoLight from '@/assets/logo_light.png'
import logoDark from '@/assets/logo_dark.png'

/**
 * Theme-aware logo component.
 * Renders logo_light.png in light mode and logo_dark.png in dark mode.
 * Tailwind's `dark:` class variant handles the swap — no JS listener needed.
 *
 * Props:
 *   size      — pixel size (number). Default 56.
 *   hoverable — adds a gentle scale/rotate/glow on hover (for navbar use).
 *   className — extra classes on the wrapper div.
 */
export default function Logo({ size = 56, className = '', hoverable = false }) {
  const hoverClass = hoverable
    ? 'transition-all duration-300 ease-out hover:scale-110 hover:rotate-3 hover:drop-shadow-[0_8px_24px_rgba(99,102,241,0.65)]'
    : ''

  return (
    <div
      className={`relative inline-flex items-center justify-center ${hoverClass} ${className}`}
      style={{ width: size, height: size }}
    >
      {/* Light-mode logo */}
      <img
        src={logoLight}
        alt="DeMaestro"
        className="absolute inset-0 w-full h-full object-contain block dark:hidden drop-shadow-md"
        draggable={false}
      />
      {/* Dark-mode logo */}
      <img
        src={logoDark}
        alt="DeMaestro"
        className="absolute inset-0 w-full h-full object-contain hidden dark:block drop-shadow-md"
        draggable={false}
      />
    </div>
  )
}
