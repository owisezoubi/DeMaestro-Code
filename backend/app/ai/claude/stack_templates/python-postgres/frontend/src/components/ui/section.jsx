/**
 * Section — vertical band of content with consistent padding.
 * Use for every distinct "block" on a page (hero, about, projects, contact, etc.).
 */
export function Section({ children, className = "", id, bg = "page" }) {
  const bgClass =
    { page: "bg-surface-page", panel: "bg-surface-panel", transparent: "" }[bg] ??
    "bg-surface-page"

  return (
    <section
      id={id}
      className={`${bgClass} py-12 sm:py-16 lg:py-20 ${className}`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {children}
      </div>
    </section>
  )
}

/**
 * SectionHeader — title + optional subtitle with consistent spacing.
 */
export function SectionHeader({ title, subtitle, align = "left" }) {
  const alignClass = align === "center" ? "text-center" : ""
  return (
    <div className={`mb-8 sm:mb-12 ${alignClass}`}>
      <h2 className="text-3xl sm:text-4xl font-bold text-text-default tracking-tight">
        {title}
      </h2>
      {subtitle && (
        <p className="mt-3 text-base sm:text-lg text-text-muted max-w-2xl">
          {subtitle}
        </p>
      )}
    </div>
  )
}
