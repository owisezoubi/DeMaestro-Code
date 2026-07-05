import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles, Code2, Rocket, GraduationCap } from 'lucide-react'
import Logo from '@/components/Logo'

// Team photos — served as static files from frontend/public/team/
// Drop the JPGs into that folder using the exact filenames below.
// See frontend/public/team/README.md for specs and fallback behavior.
// The AvatarCircle component + supervisor <img> fall back to a
// gradient initial bubble automatically if a file is missing.
const owisePhoto      = '/team/owise.jpg'
const mohamadPhoto    = '/team/mohamad.jpg'
const supervisorPhoto = '/team/natalie.jpg'

function FadeInOnScroll({ children, delay = 0 }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.classList.add('fade-in-active')
          obs.unobserve(el)
        }
      },
      { threshold: 0.15 },
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [])
  return (
    <div ref={ref} className="fade-in-prep" style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </div>
  )
}

function AvatarCircle({ photo, name, size = 'lg' }) {
  const [failed, setFailed] = useState(false)
  const isLg = size === 'lg'
  const dim = isLg ? 'w-32 h-32' : 'w-28 h-28'
  const textSize = isLg ? 'text-4xl' : 'text-3xl'

  if (photo && !failed) {
    return (
      <div className={`relative ${dim} mx-auto`}>
        <div className="absolute inset-0 rounded-full
                        bg-gradient-to-br from-accent to-accent-secondary
                        animate-spin-slow opacity-50 blur-md" />
        <img
          src={photo}
          alt={name}
          onError={() => setFailed(true)}
          className="relative w-full h-full rounded-full object-cover
                     ring-4 ring-surface-panel shadow-xl
                     group-hover:scale-105 transition-transform duration-300"
        />
      </div>
    )
  }

  return (
    <div className={`relative ${dim} mx-auto`}>
      <div className="absolute inset-0 rounded-full
                      bg-gradient-to-br from-accent to-accent-secondary
                      opacity-40 blur-md" />
      <div className={`relative w-full h-full rounded-full
                       bg-gradient-to-br from-accent to-accent-secondary
                       flex items-center justify-center
                       ring-4 ring-surface-panel shadow-xl
                       group-hover:scale-105 transition-transform duration-300`}>
        <span className={`text-white ${textSize} font-black`}>{name[0]}</span>
      </div>
    </div>
  )
}

function SupervisorCard({ photo }) {
  const [failed, setFailed] = useState(false)
  const showPhoto = photo && !failed

  return (
    <FadeInOnScroll>
      <div className="relative max-w-md mx-auto mt-8 p-8 pt-12 rounded-3xl
                      bg-surface-panel border border-surface-border
                      hover:border-accent/40 hover:shadow-2xl hover:shadow-accent/15
                      transition-all duration-500 text-center group">
        <div className="absolute top-4 left-1/2 -translate-x-1/2
                        px-3 py-1 rounded-full bg-accent/10 border border-accent/20
                        flex items-center gap-1.5 text-xs font-semibold text-accent
                        whitespace-nowrap">
          <GraduationCap className="w-3.5 h-3.5 flex-shrink-0" />
          Supervisor
        </div>

        <div className="relative w-28 h-28 mx-auto mb-5 mt-2">
          <div className="absolute inset-0 rounded-full
                          bg-gradient-to-br from-accent to-accent-secondary
                          opacity-40 blur-md" />
          {showPhoto ? (
            <img
              src={photo}
              alt="Dr. Natalie Levi"
              onError={() => setFailed(true)}
              className="relative w-full h-full rounded-full object-cover
                         ring-4 ring-surface-panel shadow-xl"
            />
          ) : (
            <div className="relative w-full h-full rounded-full
                            bg-gradient-to-br from-accent to-accent-secondary
                            flex items-center justify-center
                            ring-4 ring-surface-panel shadow-xl">
              <span className="text-white text-3xl font-black">N</span>
            </div>
          )}
        </div>
        <h3 className="text-lg font-bold text-text-default">Dr. Natalie Levi</h3>
        <p className="text-sm text-text-muted">Academic Advisor</p>
      </div>
    </FadeInOnScroll>
  )
}

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-surface-page relative">
      {/* ── Hero ──────────────────────────────────────────────────────────── */}
      <section className="relative min-h-[70vh] flex items-center justify-center overflow-hidden">
        <div className="absolute inset-0
                        bg-gradient-to-br from-accent/20 via-accent-secondary/15 to-transparent" />
        <div className="absolute inset-0
                        bg-[radial-gradient(circle_at_20%_30%,rgba(99,102,241,0.25),transparent_50%)]" />
        <div className="absolute inset-0
                        bg-[radial-gradient(circle_at_80%_70%,rgba(168,85,247,0.2),transparent_50%)]" />

        {/* Floating particles (deterministic positions) */}
        {Array.from({ length: 15 }, (_, i) => (
          <div
            key={i}
            className="absolute w-2 h-2 rounded-full bg-accent/30 animate-float-slow"
            style={{
              top: `${(i * 41 + 11) % 100}%`,
              left: `${(i * 67 + 7) % 100}%`,
              animationDelay: `${(i * 0.33) % 5}s`,
              animationDuration: `${8 + (i * 0.4) % 6}s`,
            }}
          />
        ))}

        <div className="relative max-w-3xl mx-auto px-6 text-center">
          <FadeInOnScroll>
            {/* size=160, hoverable=true, no auto-pulse */}
            <Logo size={160} hoverable className="mx-auto mb-8" />
          </FadeInOnScroll>
          <FadeInOnScroll delay={150}>
            <h1 className="text-5xl md:text-6xl font-black mb-6
                           bg-gradient-to-r from-accent via-accent-secondary to-accent
                           bg-clip-text text-transparent">
              Building Apps From Words
            </h1>
          </FadeInOnScroll>
          <FadeInOnScroll delay={300}>
            <p className="text-xl text-text-muted leading-relaxed">
              DeMaestro turns natural-language requirements into complete,
              runnable full-stack applications — generated, tested, and
              deployed automatically.
            </p>
          </FadeInOnScroll>
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────────────────── */}
      <section className="py-24 px-6 max-w-6xl mx-auto">
        <FadeInOnScroll>
          <h2 className="text-3xl md:text-4xl font-bold text-center mb-4 text-text-default">
            How it works
          </h2>
          <p className="text-text-muted text-center max-w-2xl mx-auto mb-16">
            A multi-agent pipeline that plans, builds, tests, and ships
            applications without manual intervention.
          </p>
        </FadeInOnScroll>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              icon: Sparkles,
              title: 'Understand',
              desc: 'Gemini agents extract atomic requirements and produce a structured project blueprint with a verifiable checklist.',
            },
            {
              icon: Code2,
              title: 'Build',
              desc: 'Claude generates the full codebase. Algorithmic and agentic fixers patch errors until every test passes.',
            },
            {
              icon: Rocket,
              title: 'Ship',
              desc: 'The app is packaged, deployed to Vercel with a managed Postgres, and handed back as a live URL.',
            },
          ].map((pillar, i) => (
            <FadeInOnScroll key={pillar.title} delay={i * 150}>
              <div className="group relative h-full p-8 rounded-2xl
                              bg-surface-panel border border-surface-border
                              hover:border-accent/40 hover:shadow-xl hover:shadow-accent/10
                              hover:-translate-y-1 transition-all duration-300
                              overflow-hidden">
                <div className="absolute -top-12 -right-12 w-32 h-32 rounded-full
                                bg-accent/10 blur-2xl
                                group-hover:bg-accent/20 transition-colors" />
                <div className="relative">
                  <div className="w-12 h-12 rounded-xl
                                  bg-gradient-to-br from-accent to-accent-secondary
                                  flex items-center justify-center mb-5 shadow-lg
                                  group-hover:rotate-6 group-hover:scale-110 transition-transform">
                    <pillar.icon className="w-6 h-6 text-white" />
                  </div>
                  <h3 className="text-xl font-bold text-text-default mb-2">{pillar.title}</h3>
                  <p className="text-text-muted leading-relaxed">{pillar.desc}</p>
                </div>
              </div>
            </FadeInOnScroll>
          ))}
        </div>
      </section>

      {/* ── Team ──────────────────────────────────────────────────────────── */}
      <section className="py-24 px-6 bg-gradient-to-b from-transparent via-accent/5 to-transparent">
        <div className="max-w-5xl mx-auto">
          <FadeInOnScroll>
            <h2 className="text-3xl md:text-4xl font-bold text-center mb-4 text-text-default">
              The team
            </h2>
            <p className="text-text-muted text-center mb-16">
              Software Engineering capstone · Braude College
            </p>
          </FadeInOnScroll>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mb-16">
            {[
              { name: 'Owise Zoubi', role: 'Co-developer', photo: owisePhoto },
              { name: 'Mohamad Atamneh', role: 'Co-developer', photo: mohamadPhoto },
            ].map((member, i) => (
              <FadeInOnScroll key={member.name} delay={i * 200}>
                <div className="group relative p-8 rounded-3xl
                                bg-surface-panel border border-surface-border
                                hover:border-accent/40 hover:shadow-2xl hover:shadow-accent/15
                                transition-all duration-500 text-center overflow-hidden">
                  <div className="absolute inset-0
                                  bg-gradient-to-br from-accent/0 to-accent-secondary/0
                                  group-hover:from-accent/5 group-hover:to-accent-secondary/5
                                  transition-all duration-500" />
                  <div className="relative mb-6">
                    <AvatarCircle photo={member.photo} name={member.name} size="lg" />
                  </div>
                  <h3 className="text-xl font-bold text-text-default mb-1">{member.name}</h3>
                  <p className="text-sm text-text-muted">{member.role}</p>
                </div>
              </FadeInOnScroll>
            ))}
          </div>

          {/* Supervisor — badge fully inside card (no overflow clip issue) */}
          <SupervisorCard photo={supervisorPhoto} />
        </div>
      </section>

      {/* ── CTA ───────────────────────────────────────────────────────────── */}
      <section className="py-24 px-6 text-center">
        <FadeInOnScroll>
          <h2 className="text-3xl md:text-4xl font-bold mb-6 text-text-default">
            Try it yourself
          </h2>
          <Link
            to="/dashboard"
            className="inline-flex items-center gap-2 px-8 py-4 rounded-xl
                       bg-gradient-to-r from-accent to-accent-secondary
                       text-white font-bold text-lg
                       shadow-xl shadow-accent/30
                       hover:shadow-2xl hover:shadow-accent/40
                       hover:scale-105 transition-all duration-200"
          >
            Start building
          </Link>
        </FadeInOnScroll>
      </section>
    </div>
  )
}
