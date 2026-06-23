export function Container({ children, className = "", size = "default" }) {
  const sizeClass =
    { narrow: "max-w-3xl", default: "max-w-7xl", wide: "max-w-screen-2xl", full: "max-w-none" }[
      size
    ] ?? "max-w-7xl"
  return (
    <div className={`${sizeClass} mx-auto px-4 sm:px-6 lg:px-8 ${className}`}>
      {children}
    </div>
  )
}
