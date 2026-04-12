interface EmptyStateProps {
  message: string
  hint?: string
}

export function EmptyState({ message, hint }: EmptyStateProps) {
  return (
    <div
      className="text-center py-10 rounded"
      style={{
        color: 'var(--text-muted)',
        backgroundColor: 'var(--bg-card)',
        border: '1px dashed var(--border)',
      }}
    >
      <div className="text-sm">{message}</div>
      {hint && <div className="text-xs mt-1 opacity-75">{hint}</div>}
    </div>
  )
}
