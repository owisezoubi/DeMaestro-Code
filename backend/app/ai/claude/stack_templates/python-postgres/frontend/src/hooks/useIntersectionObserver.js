import { useEffect, useRef, useState } from "react"

/**
 * Returns [ref, isIntersecting].
 * Attach ref to the element you want to observe.
 * isIntersecting flips true when the element enters the viewport.
 *
 * Options:
 *   threshold   — 0..1, default 0.1 (10% visible triggers)
 *   rootMargin  — CSS-like margin, default "0px"
 *   triggerOnce — default true; stops observing after first hit
 */
export function useIntersectionObserver({
  threshold = 0.1,
  rootMargin = "0px",
  triggerOnce = true,
} = {}) {
  const ref = useRef(null)
  const [isIntersecting, setIsIntersecting] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsIntersecting(true)
          if (triggerOnce) observer.unobserve(node)
        } else if (!triggerOnce) {
          setIsIntersecting(false)
        }
      },
      { threshold, rootMargin },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [threshold, rootMargin, triggerOnce])

  return [ref, isIntersecting]
}

export default useIntersectionObserver
