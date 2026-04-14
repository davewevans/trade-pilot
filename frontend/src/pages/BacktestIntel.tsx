import { useState, useEffect } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface CurrentConditions {
  ivr: number
  regime: string
  sma_position: string
  vix: number
}

interface HistoricalMatch {
  matching_trades: number
  win_rate: number
  avg_pnl: number
  avg_holding_days: number
  worst_trade_pnl: number
  best_trade_pnl: number
  conditions: string
}

interface EnvironmentMatchResult {
  symbol: string
  strategy: string
  current_conditions: CurrentConditions
  historical_match: HistoricalMatch | null
}

interface SimilarTrade {
  entry_date: string
  exit_date: string
  entry_ivr: number
  entry_delta: number
  entry_regime: string
  vix_at_entry: number
  holding_days: number
  pnl: number
  exit_reason: string
  similarity_score: number
}

interface SimilarTradesResult {
  trades: SimilarTrade[]
  summary: {
    profitable_count: number
    total_count: number
    winners_avg_pnl: number
    losers_avg_pnl: number
  } | null
}

interface RealityCheckResult {
  symbol: string
  strategy: string
  backtest: { win_rate: number; avg_pnl: number; total_trades: number }
  live: { win_rate: number; avg_pnl: number; total_trades: number }
  gap: {
    win_rate_delta: number
    avg_pnl_delta: number
    severity: 'LOW' | 'MODERATE' | 'HIGH'
  }
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STRATEGIES = [
  { value: 'wheel_csp', label: 'Wheel / Cash-Secured Put' },
  { value: 'bull_put_spread', label: 'Bull Put Spread' },
  { value: 'bear_call_spread', label: 'Bear Call Spread' },
  { value: 'iron_condor', label: 'Iron Condor' },
  { value: 'long_call_vertical', label: 'Long Call Vertical' },
]

const WATCHLIST_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'AAPL', 'MSFT', 'AMD', 'JPM', 'XOM']

const REGIMES = ['BULL', 'NEUTRAL', 'BEAR', 'CRASH', 'EUPHORIA']

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt$(n: number | null | undefined, decimals = 0): string {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

function fmtPct(n: number): string {
  return `${n.toFixed(1)}%`
}

function winRateColor(wr: number): string {
  if (wr >= 65) return 'var(--green)'
  if (wr >= 50) return 'var(--yellow, #eab308)'
  return 'var(--red)'
}

function severityStyle(severity: string): React.CSSProperties {
  switch (severity) {
    case 'HIGH':
      return {
        backgroundColor: 'color-mix(in srgb, var(--red) 20%, transparent)',
        color: 'var(--red)',
        border: '1px solid color-mix(in srgb, var(--red) 40%, transparent)',
      }
    case 'MODERATE':
      return {
        backgroundColor: 'color-mix(in srgb, var(--yellow, #eab308) 20%, transparent)',
        color: 'var(--yellow, #eab308)',
        border: '1px solid color-mix(in srgb, var(--yellow, #eab308) 40%, transparent)',
      }
    default:
      return {
        backgroundColor: 'color-mix(in srgb, var(--green) 20%, transparent)',
        color: 'var(--green)',
        border: '1px solid color-mix(in srgb, var(--green) 40%, transparent)',
      }
  }
}

// ── Shared UI ─────────────────────────────────────────────────────────────────

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg p-5 space-y-4"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <h2 className="font-semibold text-base" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h2>
      {children}
    </div>
  )
}

function FormRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
        {label}
      </label>
      {children}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="w-full rounded px-2 py-1.5 text-sm"
      style={inputStyle}
    />
  )
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement> & { children: React.ReactNode }) {
  const { children, ...rest } = props
  return (
    <select
      {...rest}
      className="w-full rounded px-2 py-1.5 text-sm"
      style={inputStyle}
    >
      {children}
    </select>
  )
}

function RunButton({
  onClick,
  loading,
  children,
}: {
  onClick: () => void
  loading: boolean
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="px-5 py-2 rounded font-semibold text-sm"
      style={{
        backgroundColor: loading ? 'var(--bg-secondary)' : 'var(--accent)',
        color: loading ? 'var(--text-muted)' : 'white',
        cursor: loading ? 'not-allowed' : 'pointer',
        opacity: loading ? 0.6 : 1,
      }}
    >
      {loading ? 'Loading...' : children}
    </button>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <p className="text-sm py-4 text-center" style={{ color: 'var(--text-muted)' }}>
      {message}
    </p>
  )
}

// ── Section 1: Environment Match ──────────────────────────────────────────────

function EnvironmentMatchSection() {
  const [symbol, setSymbol] = useState('SPY')
  const [strategy, setStrategy] = useState('bull_put_spread')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<EnvironmentMatchResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleCheck() {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch(
        `/api/backtest/environment-match?symbol=${symbol.trim().toUpperCase()}&strategy=${strategy}`,
        { credentials: 'include' }
      )
      if (!res.ok) {
        const j = await res.json().catch(() => ({}))
        setError(j.error ?? `HTTP ${res.status}`)
        return
      }
      setResult(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const hm = result?.historical_match
  const cc = result?.current_conditions

  return (
    <SectionCard title="Environment Match">
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        What does history say about current conditions for this strategy and symbol?
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <FormRow label="Symbol">
          <Input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="SPY"
          />
        </FormRow>
        <FormRow label="Strategy">
          <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </Select>
        </FormRow>
        <div className="flex items-end">
          <RunButton onClick={handleCheck} loading={loading}>
            Check Environment
          </RunButton>
        </div>
      </div>

      {error && (
        <p className="text-sm" style={{ color: 'var(--red)' }}>{error}</p>
      )}

      {result && cc && (
        <div className="space-y-4">
          {/* Current conditions badges */}
          <div>
            <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
              Current Conditions
            </p>
            <div className="flex flex-wrap gap-2">
              {[
                { label: 'IVR', value: cc.ivr.toFixed(1) },
                { label: 'Regime', value: cc.regime },
                { label: 'SMA', value: cc.sma_position },
                { label: 'VIX', value: cc.vix.toFixed(1) },
              ].map(({ label, value }) => (
                <span
                  key={label}
                  className="px-2 py-1 rounded text-xs font-mono"
                  style={{
                    backgroundColor: 'color-mix(in srgb, var(--accent) 12%, var(--bg-secondary))',
                    border: '1px solid color-mix(in srgb, var(--accent) 30%, var(--border))',
                    color: 'var(--text-primary)',
                  }}
                >
                  {label}: <strong>{value}</strong>
                </span>
              ))}
            </div>
          </div>

          {/* Historical match */}
          {hm == null ? (
            <div
              className="rounded p-4 text-sm"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--text-muted) 8%, transparent)',
                color: 'var(--text-muted)',
              }}
            >
              Not enough historical data (need 10+ matching trades).
            </div>
          ) : (
            <div
              className="rounded-lg p-4 space-y-3"
              style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            >
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                  Historical Match — {hm.conditions}
                </p>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {hm.matching_trades} matching trades
                </span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {[
                  { label: 'Win Rate', value: fmtPct(hm.win_rate), color: winRateColor(hm.win_rate) },
                  { label: 'Avg P&L', value: fmt$(hm.avg_pnl, 2), color: hm.avg_pnl >= 0 ? 'var(--green)' : 'var(--red)' },
                  { label: 'Avg Hold', value: `${hm.avg_holding_days.toFixed(1)}d`, color: 'var(--text-primary)' },
                  { label: 'Worst Trade', value: fmt$(hm.worst_trade_pnl, 2), color: 'var(--red)' },
                ].map(({ label, value, color }) => (
                  <div key={label}>
                    <div className="text-[10px] uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-muted)' }}>
                      {label}
                    </div>
                    <div className="text-base font-semibold font-mono" style={{ color }}>
                      {value}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  )
}

// ── Section 2: Similar Trades ─────────────────────────────────────────────────

function SimilarTradesSection() {
  const [symbol, setSymbol] = useState('SPY')
  const [strategy, setStrategy] = useState('bull_put_spread')
  const [ivr, setIvr] = useState('45')
  const [delta, setDelta] = useState('0.25')
  const [dte, setDte] = useState('28')
  const [regime, setRegime] = useState('BULL')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<SimilarTradesResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleFind() {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const params = new URLSearchParams({
        symbol: symbol.trim().toUpperCase(),
        strategy,
        ivr,
        delta,
        dte,
        regime,
      })
      const res = await fetch(`/api/backtest/similar-trades?${params}`, { credentials: 'include' })
      if (!res.ok) {
        const j = await res.json().catch(() => ({}))
        setError(j.error ?? `HTTP ${res.status}`)
        return
      }
      setResult(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const trades = result?.trades ?? []
  const summary = result?.summary

  return (
    <SectionCard title="Similar Trades">
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Find the 5 most similar historical trades to a specific setup.
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <FormRow label="Symbol">
          <Input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="SPY" />
        </FormRow>
        <FormRow label="Strategy">
          <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {STRATEGIES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </Select>
        </FormRow>
        <FormRow label="IVR">
          <Input type="number" min={0} max={100} value={ivr} onChange={(e) => setIvr(e.target.value)} />
        </FormRow>
        <FormRow label="Delta">
          <Input type="number" min={-1} max={1} step={0.01} value={delta} onChange={(e) => setDelta(e.target.value)} />
        </FormRow>
        <FormRow label="DTE">
          <Input type="number" min={1} max={120} value={dte} onChange={(e) => setDte(e.target.value)} />
        </FormRow>
        <FormRow label="Regime">
          <Select value={regime} onChange={(e) => setRegime(e.target.value)}>
            {REGIMES.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </Select>
        </FormRow>
      </div>

      <RunButton onClick={handleFind} loading={loading}>
        Find Similar
      </RunButton>

      {error && (
        <p className="text-sm" style={{ color: 'var(--red)' }}>{error}</p>
      )}

      {result && trades.length === 0 && (
        <EmptyState message="No similar trades found. The backtest database may need more data — run the weekly backtest job or perform manual backtests." />
      )}

      {trades.length > 0 && (
        <div className="space-y-3">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                  {['Entry Date', 'IVR', 'Delta', 'Regime', 'VIX', 'Days Held', 'P&L', 'Exit Reason', 'Similarity'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-muted)' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr
                    key={i}
                    style={{
                      borderBottom: '1px solid var(--border)',
                      backgroundColor: i % 2 === 0 ? 'var(--bg-card)' : 'color-mix(in srgb, var(--bg-secondary) 50%, var(--bg-card))',
                    }}
                  >
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>{t.entry_date}</td>
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>{t.entry_ivr.toFixed(1)}</td>
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>{t.entry_delta.toFixed(2)}</td>
                    <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{t.entry_regime}</td>
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-muted)' }}>{t.vix_at_entry.toFixed(1)}</td>
                    <td className="px-3 py-2" style={{ color: 'var(--text-muted)' }}>{t.holding_days}d</td>
                    <td
                      className="px-3 py-2 font-mono font-semibold"
                      style={{ color: t.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}
                    >
                      {fmt$(t.pnl, 2)}
                    </td>
                    <td className="px-3 py-2" style={{ color: 'var(--text-muted)' }}>
                      {t.exit_reason?.replace(/_/g, ' ') ?? '—'}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                        style={{
                          backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)',
                          color: 'var(--accent)',
                          border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
                        }}
                      >
                        {Math.round(t.similarity_score * 100)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {summary && (
            <div
              className="rounded p-3 text-sm"
              style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
            >
              <span style={{ color: 'var(--text-secondary)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>{summary.profitable_count} of {summary.total_count}</strong> similar trades were profitable.{' '}
                Winners avg: <strong style={{ color: 'var(--green)' }}>{fmt$(summary.winners_avg_pnl, 2)}</strong>
                {summary.total_count - summary.profitable_count > 0 && (
                  <>
                    {' '}| Losers avg: <strong style={{ color: 'var(--red)' }}>{fmt$(summary.losers_avg_pnl, 2)}</strong>
                  </>
                )}
              </span>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  )
}

// ── Section 3: Reality Check ──────────────────────────────────────────────────

function RealityCheckSection() {
  const [rows, setRows] = useState<RealityCheckResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    async function load() {
      setLoading(true)
      setError(null)
      const pairs: { symbol: string; strategy: string }[] = []
      for (const symbol of WATCHLIST_SYMBOLS) {
        for (const strategy of STRATEGIES) {
          pairs.push({ symbol, strategy: strategy.value })
        }
      }

      const results = await Promise.allSettled(
        pairs.map(({ symbol, strategy }) =>
          fetch(
            `/api/backtest/reality-check?symbol=${symbol}&strategy=${strategy}`,
            { credentials: 'include' }
          )
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null)
        )
      )

      const valid: RealityCheckResult[] = []
      for (const r of results) {
        if (r.status === 'fulfilled' && r.value != null) {
          valid.push(r.value)
        }
      }

      if (valid.length === 0 && results.every((r) => r.status === 'rejected')) {
        setError('Could not load reality check data.')
      }

      setRows(valid)
      setLoading(false)
    }

    load()
  }, [])

  return (
    <SectionCard title="Backtest vs Reality">
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Comparing backtest predictions against actual live trading performance across all active strategy/symbol pairs.
      </p>

      {loading && (
        <div className="flex items-center gap-2 py-4">
          <div
            className="w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: 'var(--accent)' }}
          />
          <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Loading reality check data...
          </span>
        </div>
      )}

      {error && (
        <p className="text-sm" style={{ color: 'var(--red)' }}>{error}</p>
      )}

      {!loading && rows.length === 0 && !error && (
        <EmptyState message="No reality check data available. Live trading data or backtest data may not yet exist for these pairs." />
      )}

      {rows.length > 0 && (
        <div className="space-y-2">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                  {['Strategy', 'Symbol', 'Backtest Win%', 'Live Win%', 'Gap', 'Severity'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-muted)' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => {
                  const smallSample = row.live.total_trades < 10
                  return (
                    <tr
                      key={`${row.strategy}-${row.symbol}`}
                      style={{
                        borderBottom: '1px solid var(--border)',
                        backgroundColor: i % 2 === 0 ? 'var(--bg-card)' : 'color-mix(in srgb, var(--bg-secondary) 50%, var(--bg-card))',
                      }}
                    >
                      <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>
                        {STRATEGIES.find((s) => s.value === row.strategy)?.label ?? row.strategy}
                      </td>
                      <td className="px-3 py-2 font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>
                        {row.symbol}
                      </td>
                      <td className="px-3 py-2 font-mono" style={{ color: winRateColor(row.backtest.win_rate) }}>
                        {fmtPct(row.backtest.win_rate)}
                        <span className="ml-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                          ({row.backtest.total_trades})
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono" style={{ color: winRateColor(row.live.win_rate) }}>
                        {fmtPct(row.live.win_rate)}
                        <span className="ml-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                          ({row.live.total_trades}{smallSample ? '*' : ''})
                        </span>
                      </td>
                      <td
                        className="px-3 py-2 font-mono"
                        style={{ color: row.gap.win_rate_delta >= 0 ? 'var(--green)' : 'var(--red)' }}
                      >
                        {row.gap.win_rate_delta >= 0 ? '+' : ''}{row.gap.win_rate_delta.toFixed(1)}pp
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className="px-2 py-0.5 rounded text-[10px] font-semibold"
                          style={severityStyle(row.gap.severity)}
                        >
                          {row.gap.severity}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {rows.some((r) => r.live.total_trades < 10) && (
            <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              * Small sample size — insufficient data for reliable comparison.
            </p>
          )}
        </div>
      )}
    </SectionCard>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function BacktestIntel() {
  return (
    <div className="p-6 max-w-screen-xl mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
          Backtest Intelligence
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Live decision-support data surfaced from the backtest database — environment match,
          similar trades, and backtest vs. reality comparison.
        </p>
      </div>

      <EnvironmentMatchSection />
      <SimilarTradesSection />
      <RealityCheckSection />
    </div>
  )
}
