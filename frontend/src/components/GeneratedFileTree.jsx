import { useState, useCallback, useEffect, useRef } from 'react'
import {
  ChevronRight, ChevronDown, Folder, File,
  Code2, FileCode, Braces, FileText, Palette,
  FileSearch, Copy, Download, WrapText,
} from 'lucide-react'
import { toast } from 'sonner'

// File-type icon resolver
function fileIcon(name) {
  const ext = name.match(/\.(\w+)$/)?.[1]?.toLowerCase()
  switch (ext) {
    case 'jsx': case 'tsx': return { Icon: Code2, color: 'text-purple-500' }
    case 'py':             return { Icon: FileCode, color: 'text-yellow-500' }
    case 'json':           return { Icon: Braces, color: 'text-orange-500' }
    case 'md':             return { Icon: FileText, color: 'text-slate-500' }
    case 'css':            return { Icon: Palette, color: 'text-pink-500' }
    case 'html':           return { Icon: FileCode, color: 'text-blue-500' }
    default:               return { Icon: File, color: 'text-text-muted' }
  }
}

function langLabel(name) {
  const ext = name.match(/\.(\w+)$/)?.[1]?.toLowerCase()
  const map = { jsx: 'JSX', tsx: 'TSX', py: 'Python', json: 'JSON', md: 'Markdown', css: 'CSS', html: 'HTML', js: 'JS', ts: 'TS', sh: 'Shell', txt: 'Text', yml: 'YAML', yaml: 'YAML', toml: 'TOML', env: 'ENV' }
  return map[ext] || (ext ? ext.toUpperCase() : 'Text')
}

// Build tree from flat path map
function buildTree(files) {
  const root = { name: '/', children: {}, isFile: false }
  for (const path of Object.keys(files)) {
    const parts = path.split('/')
    let node = root
    parts.forEach((part, i) => {
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

// Collect flat file list for keyboard nav
function flatFiles(node, acc = []) {
  const children = sortedChildren(node)
  for (const c of children) {
    if (c.isFile) acc.push(c.fullPath)
    else flatFiles(c, acc)
  }
  return acc
}

// Check if node or any descendant matches search term
function nodeMatchesSearch(node, term) {
  if (!term) return true
  if (node.isFile) return node.fullPath.toLowerCase().includes(term)
  return sortedChildren(node).some((c) => nodeMatchesSearch(c, term))
}

function TreeNode({ node, depth, onSelect, selected, searchTerm }) {
  const [open, setOpen] = useState(depth < 2)

  useEffect(() => {
    if (searchTerm) setOpen(true)
  }, [searchTerm])

  if (node.isFile) {
    if (searchTerm && !node.fullPath.toLowerCase().includes(searchTerm)) return null
    const { Icon, color } = fileIcon(node.name)
    return (
      <button
        onClick={() => onSelect(node.fullPath)}
        className={`flex items-center gap-2 w-full px-2 py-1 rounded text-left text-xs
                   hover:bg-accent/10 transition-colors
                   ${selected === node.fullPath ? 'bg-accent/15 text-accent' : 'text-text-default'}`}
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        <Icon className={`w-3.5 h-3.5 flex-shrink-0 ${color}`} />
        <span className="truncate">{node.name}</span>
      </button>
    )
  }

  if (searchTerm && !nodeMatchesSearch(node, searchTerm)) return null

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full px-2 py-1 rounded text-left text-xs
                   hover:bg-surface-border/50 text-text-muted transition-colors"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        {open
          ? <ChevronDown className="w-3 h-3 flex-shrink-0" />
          : <ChevronRight className="w-3 h-3 flex-shrink-0" />}
        <Folder className="w-3.5 h-3.5 flex-shrink-0 text-accent/60" />
        <span className="font-medium truncate">{node.name}</span>
      </button>
      {open && sortedChildren(node).map((c) => (
        <TreeNode
          key={c.fullPath || c.name}
          node={c}
          depth={depth + 1}
          onSelect={onSelect}
          selected={selected}
          searchTerm={searchTerm}
        />
      ))}
    </div>
  )
}

export default function GeneratedFileTree({ files }) {
  const [selected, setSelected] = useState(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [wordWrap, setWordWrap] = useState(false)
  const treeRef = useRef(null)

  const tree = buildTree(files)
  const content = selected ? files[selected] : null
  const lines = content ? content.split('\n') : []
  const term = searchTerm.toLowerCase().trim()

  const allFiles = flatFiles(tree)
  const currentIdx = selected ? allFiles.indexOf(selected) : -1

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      const next = allFiles[Math.min(currentIdx + 1, allFiles.length - 1)]
      if (next) setSelected(next)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const prev = allFiles[Math.max(currentIdx - 1, 0)]
      if (prev) setSelected(prev)
    } else if (e.key === 'Enter' && currentIdx >= 0) {
      // already selected — no-op
    }
  }, [allFiles, currentIdx])

  async function handleCopy() {
    if (!content) return
    try {
      await navigator.clipboard.writeText(content)
      toast.success('Copied to clipboard')
    } catch {
      toast.error('Copy failed')
    }
  }

  function handleDownload() {
    if (!content || !selected) return
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = selected.split('/').pop()
    a.click()
    URL.revokeObjectURL(url)
  }

  const fileName = selected?.split('/').pop() || ''
  const lang = langLabel(fileName)

  return (
    <div className="flex flex-col gap-2">
      {/* Search box */}
      <input
        type="text"
        placeholder="Search files…"
        value={searchTerm}
        onChange={(e) => setSearchTerm(e.target.value)}
        className="w-full px-3 py-1.5 rounded-lg text-xs
                   bg-surface-panel border border-surface-border
                   text-text-default placeholder:text-text-muted
                   focus:outline-none focus:border-accent/50 transition-colors"
      />

      <div className="grid grid-cols-12 gap-0 h-[640px]
                      rounded-xl border border-surface-border
                      bg-surface-panel overflow-hidden md:grid-cols-12">
        {/* Tree panel */}
        <div
          ref={treeRef}
          tabIndex={0}
          onKeyDown={handleKeyDown}
          className="col-span-12 md:col-span-4 border-b md:border-b-0 md:border-r
                     border-surface-border overflow-y-auto p-2 outline-none
                     h-48 md:h-full"
        >
          <p className="text-[10px] uppercase tracking-wider text-text-muted px-2 py-1.5 mb-0.5">
            Files ({Object.keys(files).length})
          </p>
          {sortedChildren(tree).map((c) => (
            <TreeNode
              key={c.name}
              node={c}
              depth={0}
              onSelect={setSelected}
              selected={selected}
              searchTerm={term}
            />
          ))}
        </div>

        {/* Viewer panel */}
        <div className="col-span-12 md:col-span-8 flex flex-col overflow-hidden h-full">
          {selected && content !== null ? (
            <>
              {/* Sticky header */}
              <div className="flex items-center gap-2 px-3 py-2
                              border-b border-surface-border bg-surface-page/70 shrink-0 flex-wrap">
                <span className="text-[10px] font-mono text-text-muted truncate flex-1 min-w-0">{selected}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent font-medium shrink-0">
                  {lang}
                </span>
                <span className="text-[10px] text-text-muted shrink-0">{lines.length} lines</span>
                <button
                  onClick={() => setWordWrap((w) => !w)}
                  title="Toggle word wrap"
                  className={`p-1 rounded transition-colors shrink-0 ${wordWrap ? 'text-accent bg-accent/10' : 'text-text-muted hover:text-text-default'}`}
                >
                  <WrapText className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={handleCopy}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px]
                             bg-surface-panel border border-surface-border
                             text-text-muted hover:text-text-default hover:border-accent/40
                             transition-colors shrink-0"
                >
                  <Copy className="w-3 h-3" />
                  Copy
                </button>
                <button
                  onClick={handleDownload}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px]
                             bg-surface-panel border border-surface-border
                             text-text-muted hover:text-text-default hover:border-accent/40
                             transition-colors shrink-0"
                >
                  <Download className="w-3 h-3" />
                  Download
                </button>
              </div>

              {/* Code panel with line numbers */}
              <div className="flex-1 overflow-auto bg-surface-page">
                <table className="w-full border-collapse text-xs font-mono leading-relaxed">
                  <tbody>
                    {lines.map((line, i) => (
                      <tr key={i} className="hover:bg-accent/5">
                        <td
                          className="select-none text-right text-text-muted pr-3 pl-3 py-px
                                     border-r border-surface-border w-10 text-[10px] align-top"
                        >
                          {i + 1}
                        </td>
                        <td
                          className={`pl-3 pr-4 py-px text-text-default align-top ${wordWrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre'}`}
                        >
                          {line || ' '}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-text-muted">
              <FileSearch className="w-10 h-10 opacity-40" />
              <p className="text-sm">Pick a file from the tree to preview its source.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
