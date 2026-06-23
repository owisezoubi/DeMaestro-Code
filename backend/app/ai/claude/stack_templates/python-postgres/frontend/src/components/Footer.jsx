export function Footer() {
  return (
    <footer className="border-t border-surface-border bg-surface-panel">
      <div className="max-w-7xl mx-auto px-4 py-6 text-sm text-text-muted">
        © {new Date().getFullYear()}
      </div>
    </footer>
  )
}

export default Footer
