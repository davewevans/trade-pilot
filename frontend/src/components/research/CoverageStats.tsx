import { useEffect, useState } from 'react'
import { StatCard } from '../shared/StatCard'

interface CoverageData {
  total_backtest_trades: number
  symbols_with_stats: number
  high_confidence_pairs: number
  low_confidence_pairs: number
  no_data_pairs: number
  oldest_trade: string | null
  newest_trade: string | null
  last_sweep_run: string | null
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return iso.slice(0, 10)
}

export function CoverageStats() {
  const [data, setData] = useState<CoverageData | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch('/api/research/winrate/coverage', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setData)
      .catch(() => setError(true))
  }, [])

  if (error) return null
  if (!data) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 animate-pulse">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="rounded-md p-4 h-16"
            style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
          />
        ))}
      </div>
    )
  }

  const lastSweep = data.last_sweep_run ? fmtDate(data.last_sweep_run) : 'never'
  const dateRange =
    data.oldest_trade && data.newest_trade
      ? `${fmtDate(data.oldest_trade)} → ${fmtDate(data.newest_trade)}`
      : '—'

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
      <StatCard
        label="Backtest Trades"
        value={data.total_backtest_trades.toLocaleString()}
        sub={dateRange}
        accent="var(--accent)"
      />
      <StatCard
        label="Symbols w/ Stats"
        value={data.symbols_with_stats.toLocaleString()}
        accent="var(--accent)"
      />
      <StatCard
        label="High Confidence"
        value={data.high_confidence_pairs.toLocaleString()}
        sub="(sym, strategy) pairs"
        accent="var(--green)"
        valueColor="var(--green)"
      />
      <StatCard
        label="Low Confidence"
        value={data.low_confidence_pairs.toLocaleString()}
        sub="(sym, strategy) pairs"
        accent="var(--yellow)"
        valueColor="var(--yellow)"
      />
      <StatCard
        label="Last Sweep Run"
        value={lastSweep}
        accent="var(--text-muted)"
      />
    </div>
  )
}
