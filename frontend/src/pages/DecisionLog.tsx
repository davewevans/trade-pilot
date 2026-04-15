import { useMemo, useState } from 'react'
import { useDecisions } from '../hooks/useDecisions'
import { useAccounts } from '../hooks/useAccounts'
import { Badge } from '../components/shared/Badge'
import { EmptyState } from '../components/shared/EmptyState'
import { LoadingSpinner } from '../components/shared/LoadingSpinner'
import type { Decision, DecisionReasoning } from '../types'

const ACTION_OPTIONS = [
  { value: '', label: 'All actions' },
  { value: 'SELL_PUT', label: 'Sell put' },
  { value: 'SELL_CALL', label: 'Sell call' },
  { value: 'OPEN', label: 'Open' },
  { value: 'ROLL', label: 'Roll' },
  { value: 'CLOSE', label: 'Close' },
  { value: 'HOLD', label: 'Hold' },
  { value: 'SKIP', label: 'Skip' },
]

const REASONING_KEYS: { key: keyof DecisionReasoning; label: string }[] = [
  { key: 'macro', label: 'Macro' },
  { key: 'fundamental', label: 'Fundamental' },
  { key: 'technical', label: 'Technical' },
  { key: 'volatility', label: 'Volatility' },
  { key: 'selection', label: 'Selection' },
  { key: 'risk', label: 'Risk' },
]

function isReasoningObject(r: Decision['reasoning']): r is DecisionReasoning {
  return r != null && typeof r === 'object'
}

function ReasoningPanel({ d }: { d: Decision }) {
  const r = d.reasoning
  if (!r) {
    return (
      <div style={{ color: 'var(--text-muted)' }} className="text-xs italic">
        No reasoning recorded.
      </div>
    )
  }

  if (isReasoningObject(r)) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {REASONING_KEYS.map(({ key, label }) => (
          <div key={key}>
            <div
              className="text-[10px] uppercase tracking-wider mb-0.5"
              style={{ color: 'var(--text-muted)' }}
            >
              {label}
            </div>
            <div className="text-xs" style={{ color: 'var(--text-primary)' }}>
              {r[key] || <span style={{ color: 'var(--text-muted)' }}>—</span>}
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="text-xs whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>
      {r}
    </div>
  )
}

function ConfidenceBar({ value }: { value: Decision['confidence'] }) {
  const n =
    typeof value === 'number'
      ? value
      : typeof value === 'string'
        ? { high: 0.9, medium: 0.6, low: 0.3 }[value.toLowerCase()] ?? null
        : null
  if (n == null) return <span style={{ color: 'var(--text-muted)' }}>—</span>

  const pct = Math.max(0, Math.min(1, n)) * 100
  const color =
    n >= 0.75 ? 'var(--green)' : n >= 0.5 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div className="flex items-center gap-2 w-28">
      <div
        className="flex-1 h-1.5 rounded-full"
        style={{ backgroundColor: 'var(--border)' }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <Badge variant="confidence" value={value ?? undefined} />
    </div>
  )
}

function DecisionRow({ d, expanded, onToggle }: {
  d: Decision
  expanded: boolean
  onToggle: () => void
}) {
  const ts = d.timestamp || d.created_at || ''
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer transition-colors hover:opacity-90"
        style={{ backgroundColor: expanded ? 'var(--bg-secondary)' : undefined }}
      >
        <td style={{ color: 'var(--text-muted)' }} className="text-xs font-mono tabular">
          {ts ? new Date(ts).toLocaleString() : '—'}
        </td>
        <td><Badge variant="neutral">{d.strategy_type}</Badge></td>
        <td className="font-mono">{d.underlying}</td>
        <td><Badge variant="action">{d.action}</Badge></td>
        <td><ConfidenceBar value={d.confidence} /></td>
        <td className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {d.skip_reason || ''}
        </td>
        <td style={{ color: 'var(--text-muted)' }}>{expanded ? '▾' : '▸'}</td>
      </tr>
      {expanded && (
        <tr style={{ backgroundColor: 'var(--bg-secondary)' }}>
          <td colSpan={7} className="p-4">
            <ReasoningPanel d={d} />
            {d.cycle_id && (
              <div className="mt-3 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                Cycle: <span className="font-mono">{d.cycle_id}</span>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export function DecisionLog() {
  const [account, setAccount] = useState<string>('')
  const [underlying, setUnderlying] = useState<string>('')
  const [action, setAction] = useState<string>('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const { accounts } = useAccounts()

  const params = useMemo(() => ({
    account: account || undefined,
    underlying: underlying.trim().toUpperCase() || undefined,
    action: action || undefined,
    limit: 100,
  }), [account, underlying, action])

  const { data, loading } = useDecisions(params)

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Decision log</h2>

      <div
        className="rounded p-3 flex flex-wrap items-center gap-3"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Account</span>
          <select
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            <option value="">All</option>
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>{a.label}</option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Underlying</span>
          <input
            value={underlying}
            onChange={(e) => setUnderlying(e.target.value)}
            placeholder="AAPL"
            className="text-xs px-2 py-1 rounded font-mono w-24"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          />
        </label>

        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: 'var(--text-muted)' }}>Action</span>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            {ACTION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>

        {data && (
          <span className="ml-auto text-xs" style={{ color: 'var(--text-muted)' }}>
            {data.total} match{data.total === 1 ? '' : 'es'}
          </span>
        )}
      </div>

      <div
        className="rounded overflow-hidden"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {loading && !data ? (
          <LoadingSpinner />
        ) : !data?.decisions.length ? (
          <EmptyState message="No decisions match these filters" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Strategy</th>
                <th>Underlying</th>
                <th>Action</th>
                <th>Confidence</th>
                <th>Skip reason</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.decisions.map((d) => (
                <DecisionRow
                  key={d.id}
                  d={d}
                  expanded={expanded === d.id}
                  onToggle={() => setExpanded(expanded === d.id ? null : d.id)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
