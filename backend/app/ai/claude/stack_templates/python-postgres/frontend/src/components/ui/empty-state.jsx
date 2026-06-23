export function EmptyState({ title, description, icon, action }) {
  return (
    <div className="text-center py-16 px-4">
      {icon && (
        <div className="mx-auto w-16 h-16 text-text-muted mb-4">{icon}</div>
      )}
      <h3 className="text-lg font-semibold text-text-default">{title}</h3>
      {description && (
        <p className="mt-2 text-sm text-text-muted max-w-sm mx-auto">{description}</p>
      )}
      {action && <div className="mt-6">{action}</div>}
    </div>
  )
}
