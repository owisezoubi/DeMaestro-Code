/**
 * HomePageExample — reference implementation showing the design system in use.
 * This file is NOT included in production builds. It serves as a concrete
 * anchor the generator can reference when uncertain about styling patterns.
 *
 * Key patterns demonstrated:
 *   - <Hero> with placeholder image and CTA buttons
 *   - <Section bg="panel"> for alternating section backgrounds
 *   - <SectionHeader> with title + subtitle
 *   - Responsive 3-column <FeatureCard> grid with hover lift
 *   - <EmptyState> for zero-data states
 *   - Correct image handling: alt text, loading, aspect ratio, object-cover
 */
import { Hero } from "@/components/ui/hero"
import { Section, SectionHeader } from "@/components/ui/section"
import {
  FeatureCard,
  FeatureCardImage,
  FeatureCardBody,
} from "@/components/ui/feature-card"
import { EmptyState } from "@/components/ui/empty-state"
import { PLACEHOLDER } from "@/lib/placeholders"

export default function HomePageExample() {
  return (
    <div>
      {/* ── Hero ── */}
      <Hero
        title="Welcome to the app"
        subtitle="A clean, modern starting point. Replace this with your real content."
        image={PLACEHOLDER.landscape(42)}
        imageAlt="App hero image"
        primaryCta={
          <button className="bg-accent text-accent-fg hover:bg-accent/90 rounded-lg px-6 py-3 font-medium transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent">
            Get started
          </button>
        }
        secondaryCta={
          <button className="bg-surface-panel border border-surface-border text-text-default hover:bg-surface-page rounded-lg px-6 py-3 font-medium transition-colors duration-200">
            Learn more
          </button>
        }
      />

      {/* ── Featured items grid ── */}
      <Section bg="panel">
        <SectionHeader
          title="Featured items"
          subtitle="Hover the cards to see the interaction baseline."
        />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 lg:gap-8">
          {[1, 2, 3].map((i) => (
            <FeatureCard key={i} href="#">
              <FeatureCardImage src={PLACEHOLDER.square(i)} alt={`Item ${i}`} />
              <FeatureCardBody>
                <h3 className="text-lg font-semibold text-text-default">Item {i}</h3>
                <p className="mt-2 text-sm text-text-muted">
                  A brief description that demonstrates muted text styling and card layout.
                </p>
                <p className="mt-4 text-sm font-medium text-accent">View details →</p>
              </FeatureCardBody>
            </FeatureCard>
          ))}
        </div>
      </Section>

      {/* ── Empty state example ── */}
      <Section>
        <SectionHeader title="Your saved items" />
        <EmptyState
          title="Nothing here yet"
          description="Browse the catalogue and save items you like — they'll appear here."
          action={
            <button className="bg-accent text-accent-fg hover:bg-accent/90 rounded-lg px-5 py-2.5 font-medium transition-colors duration-200">
              Browse catalogue
            </button>
          }
        />
      </Section>
    </div>
  )
}
