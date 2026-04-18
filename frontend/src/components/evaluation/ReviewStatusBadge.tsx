import type { ReviewStatus } from '../../api/client'

interface ReviewStatusBadgeProps {
  status: ReviewStatus
}

const STATUS_META: Record<ReviewStatus, { label: string; color: string }> = {
  'pending': { label: 'Unreviewed', color: 'var(--yellow)' },
  'reviewed': { label: 'Reviewed', color: 'var(--green)' },
  'reviewed-with-action': { label: 'Reviewed + Action', color: 'var(--accent)' },
}

export function ReviewStatusBadge({ status }: ReviewStatusBadgeProps) {
  const meta = STATUS_META[status] ?? STATUS_META['pending']
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider"
      style={{
        backgroundColor: `color-mix(in srgb, ${meta.color} 18%, transparent)`,
        color: meta.color,
        border: `1px solid color-mix(in srgb, ${meta.color} 35%, transparent)`,
      }}
    >
      {meta.label}
    </span>
  )
}
