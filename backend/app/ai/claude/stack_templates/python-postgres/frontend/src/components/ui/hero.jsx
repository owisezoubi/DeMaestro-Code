import { Container } from "./container"

/**
 * Hero — landing-style section with title, subtitle, optional CTA and image.
 * Pass `image` for a split-column hero or leave undefined for centered text.
 */
export function Hero({
  title,
  subtitle,
  primaryCta,
  secondaryCta,
  image,
  imageAlt,
  align = "center",
}) {
  const isCentered = align === "center" && !image
  return (
    <section className="relative overflow-hidden bg-surface-page py-16 sm:py-24 lg:py-32">
      <Container>
        <div
          className={`grid gap-12 ${image ? "lg:grid-cols-2 items-center" : ""} ${
            isCentered ? "text-center" : ""
          }`}
        >
          <div>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-text-default tracking-tight leading-[1.1]">
              {title}
            </h1>
            {subtitle && (
              <p
                className={`mt-6 text-lg sm:text-xl text-text-muted leading-relaxed ${
                  isCentered ? "max-w-2xl mx-auto" : "max-w-xl"
                }`}
              >
                {subtitle}
              </p>
            )}
            {(primaryCta || secondaryCta) && (
              <div
                className={`mt-8 flex flex-wrap gap-4 ${isCentered ? "justify-center" : ""}`}
              >
                {primaryCta}
                {secondaryCta}
              </div>
            )}
          </div>
          {image && (
            <div className="relative">
              <img
                src={image}
                alt={imageAlt || ""}
                loading="eager"
                className="w-full rounded-2xl shadow-xl object-cover aspect-[4/3]"
              />
            </div>
          )}
        </div>
      </Container>
    </section>
  )
}
