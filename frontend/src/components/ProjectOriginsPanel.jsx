import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, FileText, File, Cpu, AlertTriangle } from 'lucide-react'
import { getProjectRawInputs } from '../api/projects'
import { getGenerationStatus } from '../api/generation'

function useSessionCollapse(key, defaultOpen = false) {
  const [open, setOpen] = useState(() => {
    const stored = sessionStorage.getItem(key)
    return stored !== null ? stored === 'true' : defaultOpen
  })
  const toggle = () =>
    setOpen(prev => {
      const next = !prev
      sessionStorage.setItem(key, String(next))
      return next
    })
  return [open, toggle]
}

function SectionHeader({ label, open, onToggle, icon: Icon }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-full flex items-center gap-2 text-left py-3 px-4 bg-surface-panel border-b border-surface-border hover:bg-surface-page transition-colors"
    >
      {open
        ? <ChevronDown className="w-4 h-4 text-text-muted flex-shrink-0" />
        : <ChevronRight className="w-4 h-4 text-text-muted flex-shrink-0" />}
      {Icon && <Icon className="w-4 h-4 text-text-muted flex-shrink-0" />}
      <span className="text-sm font-medium text-text-default">{label}</span>
    </button>
  )
}

function RawInputsSection({ projectId }) {
  const key = `origins-inputs-${projectId}`
  const [open, toggle] = useSessionCollapse(key, true)
  const { data: inputs = [], isLoading } = useQuery({
    queryKey: ['raw-inputs', projectId],
    queryFn: () => getProjectRawInputs(projectId),
    enabled: open,
  })

  return (
    <div className="border border-surface-border rounded-lg overflow-hidden mb-3">
      <SectionHeader label="Original inputs" open={open} onToggle={toggle} icon={FileText} />
      {open && (
        <div className="bg-surface-page px-4 py-3 space-y-3">
          {isLoading ? (
            <p className="text-sm text-text-muted">Loading…</p>
          ) : inputs.length === 0 ? (
            <p className="text-sm text-text-muted">No inputs recorded.</p>
          ) : (
            inputs.map(inp => (
              <div key={inp.id} className="bg-surface-panel border border-surface-border rounded-md p-3">
                <div className="flex items-center gap-2 mb-2">
                  {inp.type === 'pdf'
                    ? <File className="w-4 h-4 text-accent flex-shrink-0" />
                    : <FileText className="w-4 h-4 text-accent flex-shrink-0" />}
                  <span className="text-xs font-semibold text-text-muted uppercase tracking-wide">
                    {inp.type}
                  </span>
                  {inp.filename && (
                    <span className="text-xs text-text-default font-mono">{inp.filename}</span>
                  )}
                  <span className="ml-auto text-xs text-text-muted">
                    {inp.char_count.toLocaleString()} chars
                  </span>
                </div>
                {inp.type === 'text' && inp.content && (
                  <pre className="text-xs text-text-default whitespace-pre-wrap font-sans leading-relaxed max-h-48 overflow-y-auto">
                    {inp.content}
                  </pre>
                )}
                {inp.type === 'pdf' && inp.extracted_text && (
                  <pre className="text-xs text-text-muted whitespace-pre-wrap font-sans leading-relaxed max-h-48 overflow-y-auto">
                    {inp.extracted_text}
                  </pre>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

function SummarySection({ summary, projectId }) {
  const key = `origins-summary-${projectId}`
  const [open, toggle] = useSessionCollapse(key, false)
  if (!summary) return null
  return (
    <div className="border border-surface-border rounded-lg overflow-hidden mb-3">
      <SectionHeader label="Project summary" open={open} onToggle={toggle} icon={FileText} />
      {open && (
        <div className="bg-surface-page px-4 py-3">
          <p className="text-sm text-text-default whitespace-pre-wrap leading-relaxed">{summary}</p>
        </div>
      )}
    </div>
  )
}

function GenerationStatusSection({ projectId }) {
  const key = `origins-genstatus-${projectId}`
  const [open, toggle] = useSessionCollapse(key, false)
  const { data: genStatus, isLoading } = useQuery({
    queryKey: ['generation-status-origins', projectId],
    queryFn: () => getGenerationStatus(projectId),
    enabled: open,
    refetchInterval: open ? 5000 : false,
  })

  return (
    <div className="border border-surface-border rounded-lg overflow-hidden mb-3">
      <SectionHeader label="Code generation status" open={open} onToggle={toggle} icon={Cpu} />
      {open && (
        <div className="bg-surface-page px-4 py-3">
          {isLoading ? (
            <p className="text-sm text-text-muted">Loading…</p>
          ) : !genStatus ? (
            <p className="text-sm text-text-muted">No status available.</p>
          ) : (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              <dt className="text-text-muted">Status</dt>
              <dd className="text-text-default font-medium">
                {genStatus.status?.replace(/_/g, ' ')}
              </dd>
              {genStatus.current_stage && (
                <>
                  <dt className="text-text-muted">Stage</dt>
                  <dd className="text-text-default">
                    {genStatus.current_stage.replace(/_/g, ' ')}
                  </dd>
                </>
              )}
              {genStatus.total_files > 0 && (
                <>
                  <dt className="text-text-muted">Files generated</dt>
                  <dd className="text-text-default">
                    {genStatus.generated_count} / {genStatus.total_files}
                  </dd>
                </>
              )}
              {genStatus.current_file && (
                <>
                  <dt className="text-text-muted">Current file</dt>
                  <dd className="text-text-default font-mono text-xs truncate">
                    {genStatus.current_file}
                  </dd>
                </>
              )}
              {genStatus.last_error && (
                <>
                  <dt className="text-text-muted">Last error</dt>
                  <dd className="text-red-600 text-xs">{genStatus.last_error}</dd>
                </>
              )}
              {genStatus.error_message && !genStatus.last_error && (
                <>
                  <dt className="text-text-muted">Error</dt>
                  <dd className="text-red-600 text-xs">{genStatus.error_message}</dd>
                </>
              )}
            </dl>
          )}
          <AdvisoryWarnings genStatus={genStatus} />
        </div>
      )}
    </div>
  )
}

function AdvisoryWarnings({ genStatus }) {
  const [showTypecheck, setShowTypecheck] = useState(false)
  const [showContract, setShowContract] = useState(false)

  if (!genStatus) return null

  const typecheckCount = genStatus.typecheck_warnings || 0
  const contractMisses = Array.isArray(genStatus.contract_advisory_misses)
    ? genStatus.contract_advisory_misses
    : []

  if (typecheckCount === 0 && contractMisses.length === 0) return null

  const totalAdvisory = (typecheckCount > 0 ? 1 : 0) + (contractMisses.length > 0 ? 1 : 0)

  return (
    <div className="mt-3 border border-amber-200 bg-amber-50 rounded-md p-3">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0" />
        <span className="text-sm font-medium text-amber-800">
          {totalAdvisory} advisory warning{totalAdvisory !== 1 ? 's' : ''} (app still shipped)
        </span>
      </div>
      <ul className="space-y-2 text-xs">
        {typecheckCount > 0 && (
          <li>
            <div className="flex items-center justify-between">
              <span className="text-amber-700">
                <span className="font-medium">typecheck:</span> {typecheckCount} mypy warning{typecheckCount !== 1 ? 's' : ''}
                {' '}— mostly SQLAlchemy Column() noise, won&apos;t affect runtime
              </span>
              {genStatus.typecheck_advisory_output && (
                <button
                  onClick={() => setShowTypecheck(v => !v)}
                  className="ml-2 text-amber-600 underline whitespace-nowrap hover:text-amber-800"
                >
                  {showTypecheck ? 'Hide' : 'Show details'}
                </button>
              )}
            </div>
            {showTypecheck && genStatus.typecheck_advisory_output && (
              <pre className="mt-1 bg-amber-100 rounded p-2 text-amber-800 font-mono text-xs overflow-x-auto whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                {genStatus.typecheck_advisory_output}
              </pre>
            )}
          </li>
        )}
        {contractMisses.length > 0 && (
          <li>
            <div className="flex items-center justify-between">
              <span className="text-amber-700">
                <span className="font-medium">contract:</span> {contractMisses.length} non-critical
                endpoint{contractMisses.length !== 1 ? 's' : ''} missing
              </span>
              <button
                onClick={() => setShowContract(v => !v)}
                className="ml-2 text-amber-600 underline whitespace-nowrap hover:text-amber-800"
              >
                {showContract ? 'Hide' : 'Show details'}
              </button>
            </div>
            {showContract && (
              <ul className="mt-1 pl-3 space-y-0.5">
                {contractMisses.map((miss, i) => (
                  <li key={i} className="text-amber-700 font-mono">{miss}</li>
                ))}
              </ul>
            )}
          </li>
        )}
      </ul>
    </div>
  )
}

export default function ProjectOriginsPanel({ projectId, project }) {
  return (
    <section className="mb-8">
      <h2 className="text-xs font-semibold text-text-muted uppercase tracking-widest mb-3">
        Project origins
      </h2>
      <RawInputsSection projectId={projectId} />
      <SummarySection summary={project?.summary} projectId={projectId} />
      <GenerationStatusSection projectId={projectId} />
    </section>
  )
}
