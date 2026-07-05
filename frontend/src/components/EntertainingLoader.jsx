import { useState, useEffect } from 'react'

const FACTS = [
  'Most of our test suites pass on cycle 1 — Claude reads the spec carefully.',
  'Claude can review 60+ files in under a minute. Your coffee isn\'t even ready yet.',
  'Don\'t worry — the LLM is procrastinating less than you would.',
  'The generated backend is fully typed and follows REST best practices.',
  'Every route gets input validation baked in automatically.',
  'Frontend components are accessible by default — aria labels included.',
  'Environment variables are never hard-coded in generated source files.',
  'The architect designs the data model before a single line of code is written.',
  'Error handling is generated for every API boundary — no silent failures.',
  'SQL schemas are generated with proper indexes for common query patterns.',
  'Authentication middleware is wired end-to-end across frontend and backend.',
  'The packager compresses your ZIP to under 2 MB on average.',
  'Claude has read more Stack Overflow than any human engineer alive.',
  'Your requirements are saved — even if you close this tab, we pick up where we left off.',
  'Fun fact: the word "bug" in computing traces back to a literal moth in a relay.',
]

const AGENTS = [
  { emoji: '🧠', label: 'Analyst' },
  { emoji: '🏗️', label: 'Architect' },
  { emoji: '✍️', label: 'Generator' },
  { emoji: '🧪', label: 'Tester' },
  { emoji: '🛠️', label: 'Debugger' },
  { emoji: '🛡️', label: 'Verifier' },
  { emoji: '📦', label: 'Packager' },
  { emoji: '🚀', label: 'Deployer' },
]

const PILLS = ['Writing source files', 'Patching errors', 'Running checks']

const CIRCUMFERENCE = 2 * Math.PI * 54

export default function EntertainingLoader({ progressPct = 0, stageLabel = 'Working', stageIcon: StageIcon }) {
  const [factIndex, setFactIndex] = useState(0)
  const [factVisible, setFactVisible] = useState(true)
  const [agentIndex, setAgentIndex] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setFactVisible(false)
      setTimeout(() => {
        setFactIndex((i) => (i + 1) % FACTS.length)
        setFactVisible(true)
      }, 350)
    }, 6000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    const interval = setInterval(() => {
      setAgentIndex((i) => (i + 1) % AGENTS.length)
    }, 2000)
    return () => clearInterval(interval)
  }, [])

  const pct = Math.min(100, Math.max(0, progressPct))
  const offset = CIRCUMFERENCE - (pct / 100) * CIRCUMFERENCE
  const agent = AGENTS[agentIndex]

  return (
    <div className="flex flex-col items-center gap-6 py-4">
      {/* Circular progress ring */}
      <div className="relative w-36 h-36">
        <svg className="w-full h-full -rotate-90" viewBox="0 0 120 120">
          {/* Track */}
          <circle
            cx="60" cy="60" r="54"
            fill="none"
            strokeWidth="8"
            className="stroke-surface-border"
          />
          {/* Progress */}
          <circle
            cx="60" cy="60" r="54"
            fill="none"
            strokeWidth="8"
            strokeLinecap="round"
            className="stroke-accent transition-all duration-700"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={offset}
          />
        </svg>
        {/* Centre content */}
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-0.5">
          {StageIcon && <StageIcon className="w-6 h-6 text-accent" />}
          <span className="text-sm font-bold text-text-default">{Math.round(pct)}%</span>
          <span className="text-[10px] text-text-muted leading-none">{stageLabel}</span>
        </div>
      </div>

      {/* Agent ticker */}
      <div className="flex items-center gap-2 px-4 py-1.5 rounded-full
                      bg-accent/10 border border-accent/20 text-sm font-medium text-accent
                      transition-all duration-300">
        <span>{agent.emoji}</span>
        <span>{agent.label}</span>
        <span className="text-text-muted font-normal">active</span>
      </div>

      {/* Rotating fact */}
      <div className="min-h-[3rem] flex items-center text-center px-6 max-w-md">
        <p
          className="text-sm text-text-muted leading-relaxed transition-all duration-350"
          style={{
            opacity: factVisible ? 1 : 0,
            transform: factVisible ? 'translateY(0)' : 'translateY(-6px)',
          }}
        >
          {FACTS[factIndex]}
        </p>
      </div>

      {/* Status pills */}
      <div className="flex items-center gap-2 flex-wrap justify-center">
        {PILLS.map((pill) => (
          <span
            key={pill}
            className="px-3 py-1 rounded-full text-xs font-medium
                       bg-surface-panel border border-surface-border
                       text-text-muted animate-pulse"
          >
            {pill}
          </span>
        ))}
      </div>
    </div>
  )
}
