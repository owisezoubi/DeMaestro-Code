/**
 * FeatureCard — generic content card with hover lift + shadow.
 * Use for project tiles, menu items, product cards, etc.
 */
export function FeatureCard({ children, className = "", href, onClick }) {
  const Tag = href ? "a" : onClick ? "button" : "div"
  const interactive = href || onClick
  return (
    <Tag
      href={href}
      onClick={onClick}
      className={`group relative block w-full text-left bg-surface-panel border border-surface-border rounded-2xl overflow-hidden ${
        interactive
          ? "transition-all duration-200 hover:-translate-y-1 hover:shadow-xl cursor-pointer focus:outline-none focus:ring-2 focus:ring-accent"
          : ""
      } ${className}`}
    >
      {children}
    </Tag>
  )
}

export function FeatureCardImage({ src, alt, aspect = "video" }) {
  const aspectClass =
    { video: "aspect-video", square: "aspect-square", photo: "aspect-[4/3]", portrait: "aspect-[3/4]" }[
      aspect
    ] ?? "aspect-video"
  return (
    <div className={`${aspectClass} w-full overflow-hidden bg-surface-border`}>
      <img
        src={src}
        alt={alt || ""}
        loading="lazy"
        className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
      />
    </div>
  )
}

export function FeatureCardBody({ children, className = "" }) {
  return <div className={`p-6 ${className}`}>{children}</div>
}
