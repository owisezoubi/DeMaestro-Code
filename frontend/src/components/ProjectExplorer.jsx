import { useState, useRef, useEffect } from 'react'
import {
  ChevronRight,
  ChevronDown,
  X,
  FolderOpen,
  Folder,
  FileCode2,
  FileText,
  FileJson,
  FileImage,
  File,
  Braces,
  Hash,
  Coffee,
  Layers,
} from 'lucide-react'

// ─── File-type helpers ────────────────────────────────────────────────────────

const EXT_META = {
  js:    { icon: FileCode2,  color: '#f0db4f', label: 'JS'   },
  jsx:   { icon: FileCode2,  color: '#61dafb', label: 'JSX'  },
  ts:    { icon: FileCode2,  color: '#3178c6', label: 'TS'   },
  tsx:   { icon: FileCode2,  color: '#3178c6', label: 'TSX'  },
  css:   { icon: Hash,       color: '#2965f1', label: 'CSS'  },
  scss:  { icon: Hash,       color: '#cd6799', label: 'SCSS' },
  html:  { icon: Layers,     color: '#e44d26', label: 'HTML' },
  json:  { icon: FileJson,   color: '#fbc02d', label: 'JSON' },
  md:    { icon: FileText,   color: '#9e9e9e', label: 'MD'   },
  py:    { icon: Coffee,     color: '#3572A5', label: 'PY'   },
  txt:   { icon: FileText,   color: '#9e9e9e', label: 'TXT'  },
  svg:   { icon: FileImage,  color: '#ffb900', label: 'SVG'  },
  png:   { icon: FileImage,  color: '#4caf50', label: 'PNG'  },
  jpg:   { icon: FileImage,  color: '#4caf50', label: 'JPG'  },
  env:   { icon: Braces,     color: '#f57c00', label: 'ENV'  },
}

function getFileMeta(filename) {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  return EXT_META[ext] || { icon: File, color: '#9e9e9e', label: ext.toUpperCase() || 'FILE' }
}

// ─── Tree builder ─────────────────────────────────────────────────────────────

function buildTree(files) {
  const root = { name: '/', children: {}, isFile: false }
  for (const path of Object.keys(files)) {
    // Normalise: strip leading slashes, split
    const parts = path.replace(/^\/+/, '').split('/')
    let node = root
    parts.forEach((part, i) => {
      if (!part) return
      if (!node.children[part]) {
        node.children[part] = {
          name: part,
          children: {},
          isFile: i === parts.length - 1,
          fullPath: parts.slice(0, i + 1).join('/'),
        }
      }
      node = node.children[part]
    })
  }
  return root
}

function sortedChildren(node) {
  return Object.values(node.children).sort((a, b) => {
    if (a.isFile !== b.isFile) return a.isFile ? 1 : -1
    return a.name.localeCompare(b.name)
  })
}

// ─── Tree node ────────────────────────────────────────────────────────────────

function TreeNode({ node, depth, onSelect, selected }) {
  const [open, setOpen] = useState(depth < 1)
  const children = sortedChildren(node)
  const meta = getFileMeta(node.name)
  const FileIcon = meta.icon

  if (node.isFile) {
    const isSelected = selected === node.fullPath
    return (
      <button
        onClick={() => onSelect(node.fullPath)}
        title={node.fullPath}
        className={`
          group flex items-center gap-2 w-full px-2 py-[5px] rounded-lg text-left text-[13px]
          transition-all duration-150 outline-none focus-visible:ring-2 focus-visible:ring-accent/50
          ${isSelected
            ? 'bg-accent/10 text-accent font-medium'
            : 'text-text-default hover:bg-surface-border/60'}
        `}
        style={{ paddingLeft: `${depth * 14 + 10}px` }}
      >
        <FileIcon
          className="w-3.5 h-3.5 flex-shrink-0 transition-transform group-hover:scale-110"
          style={{ color: meta.color }}
        />
        <span className="truncate leading-tight">{node.name}</span>
        {isSelected && (
          <span
            className="ml-auto text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-md flex-shrink-0"
            style={{ background: `${meta.color}22`, color: meta.color }}
          >
            {meta.label}
          </span>
        )}
      </button>
    )
  }

  // Folder node
  const FolderIcon = open ? FolderOpen : Folder
  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 w-full px-2 py-[5px] rounded-lg text-left text-[13px]
                   text-text-default hover:bg-surface-border/60 transition-all duration-150 outline-none
                   focus-visible:ring-2 focus-visible:ring-accent/50"
        style={{ paddingLeft: `${depth * 14 + 10}px` }}
      >
        <span className="flex-shrink-0 transition-transform duration-200" style={{ transform: open ? 'rotate(0deg)' : 'rotate(-90deg)' }}>
          <ChevronDown className="w-3 h-3 text-text-muted" />
        </span>
        <FolderIcon
          className="w-3.5 h-3.5 flex-shrink-0"
          style={{ color: open ? '#fbc02d' : '#9e9e9e' }}
        />
        <span className="font-medium truncate leading-tight">{node.name}</span>
      </button>

      {/* Animated children container */}
      <div
        className="overflow-hidden transition-all duration-200"
        style={{ maxHeight: open ? '9999px' : '0px', opacity: open ? 1 : 0 }}
      >
        {children.map(c => (
          <TreeNode
            key={c.fullPath || c.name}
            node={c}
            depth={depth + 1}
            onSelect={onSelect}
            selected={selected}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Slide-out code panel ─────────────────────────────────────────────────────

function CodePanel({ filePath, code, onClose }) {
  const panelRef = useRef(null)
  const ext = filePath?.split('.').pop()?.toLowerCase() || 'txt'
  const meta = getFileMeta(filePath || '')

  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  // Focus trap into panel when opened
  useEffect(() => {
    if (filePath) panelRef.current?.focus()
  }, [filePath])

  const lineCount = code ? code.split('\n').length : 0
  const isOpen = Boolean(filePath)

  return (
    <>
      {/* Backdrop — only on mobile, faint on desktop */}
      <div
        onClick={onClose}
        className="absolute inset-0 z-10 pointer-events-none transition-opacity duration-300"
        style={{ background: 'rgba(0,0,0,0.25)', opacity: isOpen ? 1 : 0, pointerEvents: isOpen ? 'auto' : 'none' }}
        aria-hidden="true"
      />

      {/* Slide panel */}
      <div
        ref={panelRef}
        tabIndex={-1}
        role="region"
        aria-label="File code viewer"
        className="absolute inset-y-0 right-0 z-20 flex flex-col
                   bg-surface-panel border-l border-surface-border
                   transition-all duration-300 ease-out focus:outline-none"
        style={{
          width: isOpen ? '62%' : '0%',
          minWidth: isOpen ? '320px' : '0px',
          opacity: isOpen ? 1 : 0,
          overflow: 'hidden',
          boxShadow: isOpen ? '-8px 0 32px rgba(0,0,0,0.12)' : 'none',
        }}
      >
        {filePath && (
          <>
            {/* Top bar */}
            <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-surface-border bg-surface-page/60 backdrop-blur-sm flex-shrink-0">
              <div className="flex items-center gap-2 min-w-0">
                <meta.icon className="w-4 h-4 flex-shrink-0" style={{ color: meta.color }} />
                <span className="text-xs font-mono text-text-default truncate">{filePath}</span>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                <span className="text-[11px] text-text-muted font-mono">{lineCount} lines</span>
                <span
                  className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
                  style={{ background: `${meta.color}22`, color: meta.color }}
                >
                  {meta.label}
                </span>
                <button
                  onClick={onClose}
                  className="p-1 rounded-md text-text-muted hover:text-text-default
                             hover:bg-surface-border/80 transition-colors"
                  aria-label="Close code panel"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Code body */}
            <div className="flex-1 overflow-auto">
              <table className="w-full border-collapse text-[12px] font-mono">
                <tbody>
                  {(code || '').split('\n').map((line, i) => (
                    <tr
                      key={i}
                      className="group hover:bg-accent/5 transition-colors"
                    >
                      <td
                        className="select-none text-right pr-4 pl-4 py-px text-text-muted/50
                                   border-r border-surface-border/40 w-[3.5rem] align-top
                                   group-hover:text-text-muted"
                        style={{ lineHeight: '1.6' }}
                      >
                        {i + 1}
                      </td>
                      <td
                        className="pl-4 pr-4 py-px text-text-default whitespace-pre align-top"
                        style={{ lineHeight: '1.6' }}
                      >
                        {line || ' '}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </>
  )
}

// ─── Main export ──────────────────────────────────────────────────────────────

export default function ProjectExplorer({ files }) {
  const [selected, setSelected] = useState(null)
  const tree = buildTree(files)
  const fileCount = Object.keys(files).length

  function handleSelect(path) {
    // Toggle: click same file again closes the panel
    setSelected(prev => (prev === path ? null : path))
  }

  function handleClose() {
    setSelected(null)
  }

  // Normalise the selected key to match whatever format `files` uses
  const lookupKey =
    selected
      ? Object.keys(files).find(
          k => k.replace(/^\/+/, '') === selected || k === selected || k === `/${selected}`
        ) || null
      : null

  const code = lookupKey ? files[lookupKey] : null

  return (
    <div
      className="relative flex overflow-hidden rounded-xl border border-surface-border bg-surface-panel"
      style={{ height: '520px' }}
    >
      {/* ── Left: Folder tree ── */}
      <div
        className="flex flex-col flex-shrink-0 overflow-hidden border-r border-surface-border
                   transition-all duration-300"
        style={{ width: selected ? '38%' : '100%' }}
      >
        {/* Tree header */}
        <div className="flex items-center justify-between px-3 py-2.5 border-b border-surface-border bg-surface-page/40 flex-shrink-0">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-text-muted">
            Project Explorer
          </span>
          <span className="text-[11px] text-text-muted tabular-nums">
            {fileCount} file{fileCount !== 1 ? 's' : ''}
          </span>
        </div>

        {/* Scrollable tree */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden p-2 space-y-0.5">
          {sortedChildren(tree).map(c => (
            <TreeNode
              key={c.name}
              node={c}
              depth={0}
              onSelect={handleSelect}
              selected={selected}
            />
          ))}
        </div>

        {/* Bottom hint */}
        <div className="flex-shrink-0 px-3 py-2 border-t border-surface-border/60">
          <p className="text-[10px] text-text-muted/60 italic">
            {selected ? 'Click the same file to close' : 'Click any file to view its code →'}
          </p>
        </div>
      </div>

      {/* ── Right: Slide-out code panel ── */}
      <CodePanel
        filePath={selected}
        code={code}
        onClose={handleClose}
      />
    </div>
  )
}
