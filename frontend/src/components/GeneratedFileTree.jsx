import { useState } from 'react'
import { ChevronRight, ChevronDown, File, Folder } from 'lucide-react'

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

function Node({ node, depth, files, onSelect, selected }) {
  const [open, setOpen] = useState(depth < 2)
  const children = sortedChildren(node)

  if (node.isFile) {
    return (
      <button
        onClick={() => onSelect(node.fullPath)}
        className={`flex items-center gap-2 w-full px-2 py-1 rounded text-left text-sm
                   hover:bg-accent/10 transition-colors
                   ${selected === node.fullPath
                     ? 'bg-accent/15 text-accent'
                     : 'text-text-default'}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <File className="w-3.5 h-3.5 flex-shrink-0 opacity-60" />
        <span className="truncate">{node.name}</span>
      </button>
    )
  }

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full px-2 py-1 rounded text-left text-sm
                   hover:bg-surface-border/50 text-text-default transition-colors"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {open
          ? <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />}
        <Folder className="w-3.5 h-3.5 flex-shrink-0 opacity-60" />
        <span className="font-medium truncate">{node.name}</span>
      </button>
      {open && children.map((c) => (
        <Node
          key={c.fullPath || c.name}
          node={c}
          depth={depth + 1}
          files={files}
          onSelect={onSelect}
          selected={selected}
        />
      ))}
    </div>
  )
}

export default function GeneratedFileTree({ files }) {
  const [selected, setSelected] = useState(null)
  const tree = buildTree(files)
  const content = selected ? files[selected] : null
  const lang = selected?.match(/\.(\w+)$/)?.[1] || 'text'

  return (
    <div className="grid grid-cols-12 gap-0 h-[560px]
                    rounded-xl border border-surface-border
                    bg-surface-panel overflow-hidden">
      {/* Tree panel */}
      <div className="col-span-4 border-r border-surface-border overflow-y-auto p-2">
        <p className="text-xs uppercase tracking-wider text-text-muted px-2 py-2 mb-1">
          Files ({Object.keys(files).length})
        </p>
        {sortedChildren(tree).map((c) => (
          <Node
            key={c.name}
            node={c}
            depth={0}
            files={files}
            onSelect={setSelected}
            selected={selected}
          />
        ))}
      </div>

      {/* Viewer panel */}
      <div className="col-span-8 flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="flex items-center justify-between px-4 py-2.5
                            border-b border-surface-border bg-surface-page/50 shrink-0">
              <span className="text-xs font-mono text-text-default truncate">{selected}</span>
              <span className="text-xs text-text-muted uppercase ml-4 shrink-0">{lang}</span>
            </div>
            <pre className="flex-1 overflow-auto p-4 text-xs font-mono
                            text-text-default bg-surface-page leading-relaxed">
              <code>{content}</code>
            </pre>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            Select a file to view its contents
          </div>
        )}
      </div>
    </div>
  )
}
