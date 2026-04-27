import { useEffect, useRef, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

// ── Types ─────────────────────────────────────────────────────────────────────

interface BacktestParams {
  strategy: string
  symbols: string[]
  start_date: string
  end_date: string
  delta: number
  dte_min: number
  dte_max: number
  ivr_threshold: number
  profit_close_pct: number
  contracts: number
  spread_width_strikes: number
  slippage_model: string
}

interface SimulatedTrade {
  symbol: string
  strategy: string
  entry_date: string
  exit_date: string | null
  expiration_date: string
  short_strike: number
  long_strike: number | null
  short_strike_2: number | null
  long_strike_2: number | null
  entry_credit: number
  exit_debit: number | null
  contracts: number
  pnl: number | null
  exit_reason: string | null
  entry_delta: number
  entry_ivr: number
  entry_regime: string
  entry_iv_env: string
  holding_days: number | null
  vix_at_entry: number | null
  sma50_position: string | null
}

interface BacktestResult {
  params: BacktestParams
  total_pnl: number
  win_rate: number
  avg_trade_pnl: number
  max_drawdown: number
  avg_duration_days: number
  total_trades: number
  winning_trades: number
  sharpe_ratio: number | null
  total_slippage_cost: number | null
  monthly_returns: { month: string; pnl: number }[]
  equity_curve: { date: string; cumulative_pnl: number }[]
  trades: SimulatedTrade[]
}

interface BacktestRunSummary {
  id: string
  strategy: string
  symbols: string[]
  start_date: string
  end_date: string
  total_pnl: number
  win_rate: number
  sharpe_ratio: number | null
  total_trades: number
  created_at: string
  slippage_model: string
}

interface JobStatus {
  job_id: string
  status: 'queued' | 'running' | 'complete' | 'error'
  progress: string
  error?: string | null
  result?: BacktestResult
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STRATEGIES = [
  { value: 'wheel_csp', label: 'Wheel / Cash-Secured Put' },
  { value: 'bull_put_spread', label: 'Bull Put Spread' },
  { value: 'bear_call_spread', label: 'Bear Call Spread' },
  { value: 'iron_condor', label: 'Iron Condor' },
  { value: 'iron_butterfly', label: 'Iron Butterfly' },
  { value: 'long_call_vertical', label: 'Long Call Vertical' },
]

const PRESET_SYMBOLS: { label: string; syms: string[] }[] = [
  { label: 'Index ETFs', syms: ['SPY', 'QQQ', 'IWM'] },
  { label: 'Tech', syms: ['AAPL', 'MSFT', 'NVDA', 'AMD'] },
  { label: 'Financials', syms: ['JPM', 'GS', 'BAC'] },
  { label: 'Watchlist (all)', syms: ['SPY', 'QQQ', 'IWM', 'AAPL', 'MSFT', 'AMD', 'JPM', 'XOM'] },
]

const DEFAULT_PARAMS: BacktestParams = {
  strategy: 'bull_put_spread',
  symbols: ['SPY'],
  start_date: '2023-01-01',
  end_date: '2024-12-31',
  delta: 0.30,
  dte_min: 21,
  dte_max: 45,
  ivr_threshold: 30,
  profit_close_pct: 0.50,
  contracts: 1,
  spread_width_strikes: 5,
  slippage_model: 'orats',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt$(n: number | null | undefined, decimals = 0): string {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${n.toFixed(1)}%`
}

function exitReasonBadge(reason: string | null): string {
  switch (reason) {
    case 'profit_target': return 'Profit'
    case 'dte_expired': return 'DTE'
    case 'max_loss': return 'Stop'
    case 'still_open': return 'Open'
    default: return reason ?? '—'
  }
}

function exitReasonColor(reason: string | null): string {
  switch (reason) {
    case 'profit_target': return 'var(--green)'
    case 'dte_expired': return 'var(--text-muted)'
    case 'max_loss': return 'var(--red)'
    default: return 'var(--text-secondary)'
  }
}

async function postJson(path: string, body: unknown): Promise<Response> {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
  })
}

// ── Main component ────────────────────────────────────────────────────────────

interface ConfirmHeavy {
  estimate: number
  remaining: number
  message: string
}

export function Backtest() {
  const [params, setParams] = useState<BacktestParams>(DEFAULT_PARAMS)
  const [symbolInput, setSymbolInput] = useState('SPY')
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<BacktestRunSummary[]>([])
  const [historyResult, setHistoryResult] = useState<BacktestResult | null>(null)
  const [confirmHeavy, setConfirmHeavy] = useState<ConfirmHeavy | null>(null)
  const [alreadyRunning, setAlreadyRunning] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Load history on mount ─────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/backtest/history', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : []))
      .then((data: BacktestRunSummary[]) => setHistory(data.slice(0, 20)))
      .catch(() => {})
  }, [])

  // ── Polling ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/backtest/${jobId}`, { credentials: 'include' })
        if (!res.ok) return
        const data: JobStatus = await res.json()
        setJob(data)
        if (data.status === 'complete' || data.status === 'error') {
          if (pollRef.current) clearInterval(pollRef.current)
          // Refresh history after a completed run
          if (data.status === 'complete') {
            fetch('/api/backtest/history', { credentials: 'include' })
              .then((r) => (r.ok ? r.json() : []))
              .then((d: BacktestRunSummary[]) => setHistory(d.slice(0, 20)))
              .catch(() => {})
          }
        }
      } catch (_e) {
        // ignore transient errors
      }
    }, 2000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [jobId])

  // ── Handlers ─────────────────────────────────────────────────────────────
  function set<K extends keyof BacktestParams>(key: K, val: BacktestParams[K]) {
    setParams((p) => ({ ...p, [key]: val }))
  }

  function addSymbol(sym: string) {
    const s = sym.trim().toUpperCase()
    if (!s || params.symbols.includes(s)) return
    set('symbols', [...params.symbols, s])
  }

  function removeSymbol(sym: string) {
    set('symbols', params.symbols.filter((s) => s !== sym))
  }

  function applyPreset(syms: string[]) {
    set('symbols', syms)
  }

  async function handleRun(confirmHeavyFlag = false) {
    setError(null)
    setJob(null)
    setJobId(null)
    setHistoryResult(null)
    setConfirmHeavy(null)
    setAlreadyRunning(null)
    if (pollRef.current) clearInterval(pollRef.current)

    try {
      const body = confirmHeavyFlag ? { ...params, confirm_heavy: true } : params
      const res = await postJson('/api/backtest', body)
      const j = await res.json().catch(() => ({}))

      if (res.status === 202 && j.status === 'confirm_required') {
        // Server wants confirmation before proceeding with a heavy backtest
        setConfirmHeavy({ estimate: j.estimate, remaining: j.remaining, message: j.message })
        return
      }

      if (res.status === 409) {
        // Another backtest is already running
        setAlreadyRunning(j.since ?? 'unknown time')
        return
      }

      if (res.status === 400) {
        setError(j.message ?? j.error ?? `HTTP ${res.status}`)
        return
      }

      if (!res.ok) {
        setError(j.error ?? `HTTP ${res.status}`)
        return
      }

      setJobId(j.job_id)
    } catch (e: unknown) {
      setError(String(e))
    }
  }

  async function handleConfirmAndRun() {
    await handleRun(true)
  }

  async function handleLoadHistoryRun(id: string) {
    try {
      const res = await fetch(`/api/backtest/${id}/trades`, { credentials: 'include' })
      if (!res.ok) return
      const data: BacktestResult = await res.json()
      setHistoryResult(data)
      setJob(null)
      setJobId(null)
    } catch (_e) {}
  }

  const result = job?.result ?? historyResult
  const running = job?.status === 'queued' || job?.status === 'running'

  return (
    <div className="p-6 max-w-screen-xl mx-auto space-y-8">
      <div>
        <h1 className="text-xl font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
          Strategy Backtester
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Simulate how a strategy would have performed using ORATS historical options data.
          First run fetches from the ORATS API and caches locally — subsequent runs are fast.{' '}
          <a
            href="https://orats.com/university/backtesting-methodology"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2"
            style={{ color: 'var(--accent)' }}
          >
            Learn about ORATS backtesting methodology →
          </a>
        </p>
      </div>

      {/* ── Parameters panel ───────────────────────────────────────────────── */}
      <div
        className="rounded-lg p-5 space-y-5"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <h2 className="font-semibold text-sm uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
          Parameters
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* Strategy */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Strategy
            </label>
            <select
              value={params.strategy}
              onChange={(e) => set('strategy', e.target.value)}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>

          {/* Date range */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Start Date
            </label>
            <input
              type="date"
              value={params.start_date}
              onChange={(e) => set('start_date', e.target.value)}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              End Date
            </label>
            <input
              type="date"
              value={params.end_date}
              onChange={(e) => set('end_date', e.target.value)}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>

          {/* Delta */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Short Strike Delta (e.g. 0.30)
            </label>
            <input
              type="number"
              min={0.05} max={0.50} step={0.05}
              value={params.delta}
              onChange={(e) => set('delta', parseFloat(e.target.value))}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>

          {/* DTE range */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              DTE Range (min / max)
            </label>
            <div className="flex gap-2">
              <input
                type="number" min={1} max={90}
                value={params.dte_min}
                onChange={(e) => set('dte_min', parseInt(e.target.value))}
                className="w-full rounded px-2 py-1.5 text-sm"
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                }}
              />
              <input
                type="number" min={1} max={120}
                value={params.dte_max}
                onChange={(e) => set('dte_max', parseInt(e.target.value))}
                className="w-full rounded px-2 py-1.5 text-sm"
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
          </div>

          {/* IVR threshold */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Min IV Rank to Enter (%)
            </label>
            <input
              type="number" min={0} max={100}
              value={params.ivr_threshold}
              onChange={(e) => set('ivr_threshold', parseFloat(e.target.value))}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>

          {/* Profit close % */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Close at % of Credit Captured
            </label>
            <div className="flex items-center gap-2">
              <input
                type="range" min={10} max={90} step={5}
                value={Math.round(params.profit_close_pct * 100)}
                onChange={(e) => set('profit_close_pct', parseInt(e.target.value) / 100)}
                className="flex-1"
              />
              <span className="text-sm font-mono w-10" style={{ color: 'var(--text-primary)' }}>
                {Math.round(params.profit_close_pct * 100)}%
              </span>
            </div>
          </div>

          {/* Spread width */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Spread Width (strikes)
            </label>
            <input
              type="number" min={1} max={50}
              value={params.spread_width_strikes}
              onChange={(e) => set('spread_width_strikes', parseInt(e.target.value))}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>

          {/* Contracts */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Contracts per Trade
            </label>
            <input
              type="number" min={1} max={100}
              value={params.contracts}
              onChange={(e) => set('contracts', parseInt(e.target.value))}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
          </div>

          {/* Slippage model */}
          <div className="space-y-1">
            <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              Slippage Model
            </label>
            <select
              value={params.slippage_model}
              onChange={(e) => set('slippage_model', e.target.value)}
              className="w-full rounded px-2 py-1.5 text-sm"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            >
              <option value="orats">ORATS Realistic (recommended)</option>
              <option value="none">None (raw mid prices)</option>
            </select>
            <p className="text-[10px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              ORATS model applies 75% bid-ask slippage for single legs, 66% for 2-leg spreads,
              53% for iron condors — based on 20 years of market-making experience.
            </p>
          </div>
        </div>

        {/* Symbol selector */}
        <div className="space-y-2">
          <label className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
            Symbols
          </label>
          <div className="flex flex-wrap gap-2 mb-2">
            {PRESET_SYMBOLS.map((g) => (
              <button
                key={g.label}
                onClick={() => applyPreset(g.syms)}
                className="text-xs px-2 py-1 rounded"
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                {g.label}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              type="text"
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  addSymbol(symbolInput)
                  setSymbolInput('')
                }
              }}
              placeholder="e.g. SPY"
              className="rounded px-2 py-1.5 text-sm font-mono w-28"
              style={{
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
            />
            <button
              onClick={() => { addSymbol(symbolInput); setSymbolInput('') }}
              className="px-3 py-1.5 rounded text-sm"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--accent) 20%, var(--bg-secondary))',
                border: '1px solid var(--accent)',
                color: 'var(--accent)',
              }}
            >
              Add
            </button>
          </div>
          <div className="flex flex-wrap gap-2 mt-1">
            {params.symbols.map((sym) => (
              <span
                key={sym}
                className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono"
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--accent) 12%, var(--bg-secondary))',
                  border: '1px solid color-mix(in srgb, var(--accent) 30%, var(--border))',
                  color: 'var(--text-primary)',
                }}
              >
                {sym}
                <button
                  onClick={() => removeSymbol(sym)}
                  className="ml-1"
                  style={{ color: 'var(--text-muted)' }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>

        {/* Run button */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <button
              onClick={() => handleRun()}
              disabled={running || params.symbols.length === 0}
              className="px-6 py-2 rounded font-semibold text-sm transition-all"
              style={{
                backgroundColor: running ? 'var(--bg-secondary)' : 'var(--accent)',
                color: running ? 'var(--text-muted)' : 'white',
                cursor: running ? 'not-allowed' : 'pointer',
                opacity: running ? 0.6 : 1,
              }}
            >
              {running ? 'Running...' : 'Run Backtest'}
            </button>
            {error && (
              <span className="text-sm" style={{ color: 'var(--red)' }}>{error}</span>
            )}
          </div>

          {/* 202 confirm_required dialog */}
          {confirmHeavy && (
            <div
              className="rounded-lg p-4 space-y-3"
              style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--yellow, #f59e0b)' }}
            >
              <p className="text-sm font-semibold" style={{ color: 'var(--yellow, #f59e0b)' }}>
                Heavy ORATS usage — confirm before proceeding
              </p>
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                {confirmHeavy.message}
              </p>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Estimated calls: <strong>{confirmHeavy.estimate.toLocaleString()}</strong> of{' '}
                <strong>{confirmHeavy.remaining.toLocaleString()}</strong> remaining this month.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={handleConfirmAndRun}
                  className="px-4 py-1.5 rounded text-sm font-semibold"
                  style={{ backgroundColor: 'var(--accent)', color: 'white' }}
                >
                  Confirm and run
                </button>
                <button
                  onClick={() => setConfirmHeavy(null)}
                  className="px-4 py-1.5 rounded text-sm"
                  style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* 409 already running banner */}
          {alreadyRunning && (
            <div
              className="rounded-lg p-4"
              style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
            >
              <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                A backtest is already running (started {alreadyRunning}). Please wait for it to complete before submitting a new one.
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ── Progress ─────────────────────────────────────────────────────────── */}
      {job && (job.status === 'queued' || job.status === 'running') && (
        <div
          className="rounded-lg p-4 space-y-2"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center gap-2">
            <div
              className="w-2 h-2 rounded-full animate-pulse"
              style={{ backgroundColor: 'var(--accent)' }}
            />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              {job.status === 'queued' ? 'Queued' : 'Running'}
            </span>
          </div>
          <p className="text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>
            {job.progress}
          </p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Fetching ORATS historical data... This may take 30–120 seconds on first run.
            Subsequent runs use cached data and are much faster.
          </p>
        </div>
      )}

      {/* ── Error ────────────────────────────────────────────────────────────── */}
      {job?.status === 'error' && (
        <div
          className="rounded-lg p-4"
          style={{ backgroundColor: 'color-mix(in srgb, var(--red) 10%, var(--bg-card))', border: '1px solid var(--red)' }}
        >
          <p className="text-sm font-medium" style={{ color: 'var(--red)' }}>Backtest failed</p>
          <p className="text-xs mt-1 font-mono" style={{ color: 'var(--text-secondary)' }}>
            {job.error}
          </p>
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────────── */}
      {result && (
        <div>
          {historyResult && (
            <div className="flex items-center justify-between mb-4">
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Viewing saved backtest
              </p>
              <button
                onClick={() => setHistoryResult(null)}
                className="text-xs px-2 py-1 rounded"
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                Clear
              </button>
            </div>
          )}
          <BacktestResults result={result} />
        </div>
      )}

      {/* ── Past Backtests ────────────────────────────────────────────────────── */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ border: '1px solid var(--border)' }}
      >
        <div
          className="px-4 py-3"
          style={{ backgroundColor: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}
        >
          <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
            Past Backtests
          </h3>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
            Click a row to load its results
          </p>
        </div>

        {history.length === 0 ? (
          <div className="px-4 py-8 text-center" style={{ backgroundColor: 'var(--bg-card)' }}>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              No saved backtests yet. Run one above and it will appear here.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                  {['Date Run', 'Strategy', 'Symbols', 'Period', 'Trades', 'Win Rate', 'P&L', 'Sharpe'].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-muted)' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((run, i) => (
                  <tr
                    key={run.id}
                    onClick={() => handleLoadHistoryRun(run.id)}
                    className="cursor-pointer"
                    style={{
                      borderBottom: '1px solid var(--border)',
                      backgroundColor: i % 2 === 0 ? 'var(--bg-card)' : 'color-mix(in srgb, var(--bg-secondary) 50%, var(--bg-card))',
                    }}
                    onMouseEnter={(e) => {
                      ;(e.currentTarget as HTMLTableRowElement).style.backgroundColor =
                        'color-mix(in srgb, var(--accent) 8%, var(--bg-card))'
                    }}
                    onMouseLeave={(e) => {
                      ;(e.currentTarget as HTMLTableRowElement).style.backgroundColor =
                        i % 2 === 0 ? 'var(--bg-card)' : 'color-mix(in srgb, var(--bg-secondary) 50%, var(--bg-card))'
                    }}
                  >
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-muted)' }}>
                      {run.created_at.slice(0, 10)}
                    </td>
                    <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>
                      {STRATEGIES.find((s) => s.value === run.strategy)?.label ?? run.strategy}
                    </td>
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                      {Array.isArray(run.symbols) ? run.symbols.join(', ') : run.symbols}
                    </td>
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-muted)' }}>
                      {run.start_date.slice(0, 7)} – {run.end_date.slice(0, 7)}
                    </td>
                    <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>
                      {run.total_trades}
                    </td>
                    <td
                      className="px-3 py-2 font-mono"
                      style={{ color: run.win_rate >= 60 ? 'var(--green)' : run.win_rate >= 50 ? 'var(--text-secondary)' : 'var(--red)' }}
                    >
                      {fmtPct(run.win_rate)}
                    </td>
                    <td
                      className="px-3 py-2 font-mono font-semibold"
                      style={{ color: run.total_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}
                    >
                      {fmt$(run.total_pnl)}
                    </td>
                    <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                      {run.sharpe_ratio != null ? run.sharpe_ratio.toFixed(2) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Results view ──────────────────────────────────────────────────────────────

function BacktestResults({ result }: { result: BacktestResult }) {
  const [tradeFilter, setTradeFilter] = useState<string>('all')

  const filteredTrades = result.trades.filter((t) => {
    if (tradeFilter === 'wins') return t.pnl != null && t.pnl > 0
    if (tradeFilter === 'losses') return t.pnl != null && t.pnl < 0
    return true
  })

  return (
    <div className="space-y-6">
      {/* ── Summary stats ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <StatCard label="Total P&L" value={fmt$(result.total_pnl)} positive={result.total_pnl >= 0} />
        <StatCard label="Win Rate" value={fmtPct(result.win_rate)} positive={result.win_rate >= 50} />
        <StatCard label="Avg Trade" value={fmt$(result.avg_trade_pnl)} positive={result.avg_trade_pnl >= 0} />
        <StatCard label="Max Drawdown" value={fmt$(-result.max_drawdown)} positive={false} negative />
        <StatCard label="Avg Duration" value={`${result.avg_duration_days.toFixed(1)}d`} />
        <StatCard label="Total Trades" value={String(result.total_trades)} />
        <StatCard label="Winners" value={`${result.winning_trades}/${result.total_trades}`} positive={result.winning_trades >= result.total_trades / 2} />
        <StatCard
          label="Sharpe"
          value={result.sharpe_ratio != null ? result.sharpe_ratio.toFixed(2) : '—'}
          positive={result.sharpe_ratio != null && result.sharpe_ratio >= 1.0}
        />
        <StatCard
          label="Slippage Cost"
          value={result.total_slippage_cost != null ? fmt$(-result.total_slippage_cost) : '—'}
          negative
        />
      </div>

      {/* ── Equity curve ──────────────────────────────────────────────────── */}
      {result.equity_curve.length > 0 && (
        <ChartCard title="Cumulative P&L">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={result.equity_curve} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                tickFormatter={(v: string) => v.slice(0, 7)}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                tickFormatter={(v: number) => `$${v >= 0 ? '+' : ''}${v.toLocaleString()}`}
                width={72}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border)',
                  fontSize: 12,
                }}
                formatter={(v: number) => [fmt$(v), 'P&L']}
              />
              <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="4 2" />
              <Line
                type="monotone"
                dataKey="cumulative_pnl"
                stroke="var(--accent)"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      )}

      {/* ── Monthly returns ────────────────────────────────────────────────── */}
      {result.monthly_returns.length > 0 && (
        <ChartCard title="Monthly Returns">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={result.monthly_returns} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="month"
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                interval={0}
                angle={-45}
                textAnchor="end"
                height={40}
              />
              <YAxis
                tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                tickFormatter={(v: number) => `$${v >= 0 ? '' : ''}${v.toLocaleString()}`}
                width={70}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--border)',
                  fontSize: 12,
                }}
                formatter={(v: number) => [fmt$(v), 'P&L']}
              />
              <ReferenceLine y={0} stroke="var(--border)" />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                {result.monthly_returns.map((entry, idx) => (
                  <Cell
                    key={idx}
                    fill={entry.pnl >= 0 ? 'var(--green)' : 'var(--red)'}
                    fillOpacity={0.8}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      )}

      {/* ── Trade table ───────────────────────────────────────────────────── */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ border: '1px solid var(--border)' }}
      >
        <div
          className="px-4 py-3 flex items-center justify-between"
          style={{ backgroundColor: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}
        >
          <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
            Trade Log ({filteredTrades.length} trades)
          </h3>
          <div className="flex gap-1">
            {(['all', 'wins', 'losses'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setTradeFilter(f)}
                className="px-2 py-1 rounded text-xs capitalize"
                style={{
                  backgroundColor: tradeFilter === f ? 'var(--accent)' : 'var(--bg-secondary)',
                  color: tradeFilter === f ? 'white' : 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                }}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
                {['Symbol', 'Entry', 'Exit', 'Expiry', 'Strike', 'Credit', 'Close', 'P&L', 'Days', 'IVR', 'VIX', 'SMA', 'Regime', 'Exit Reason'].map((h) => (
                  <th key={h} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--text-muted)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((t, i) => (
                <tr
                  key={i}
                  style={{
                    borderBottom: '1px solid var(--border)',
                    backgroundColor: i % 2 === 0 ? 'var(--bg-card)' : 'color-mix(in srgb, var(--bg-secondary) 50%, var(--bg-card))',
                  }}
                >
                  <td className="px-3 py-2 font-mono font-semibold" style={{ color: 'var(--text-primary)' }}>
                    {t.symbol}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                    {t.entry_date}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                    {t.exit_date ?? '—'}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-muted)' }}>
                    {t.expiration_date}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                    {t.short_strike}{t.long_strike ? `/${t.long_strike}` : ''}
                    {t.short_strike_2 ? `/${t.short_strike_2}` : ''}
                    {t.long_strike_2 ? `/${t.long_strike_2}` : ''}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                    ${Math.abs(t.entry_credit).toFixed(2)}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>
                    {t.exit_debit != null ? `$${t.exit_debit.toFixed(2)}` : '—'}
                  </td>
                  <td
                    className="px-3 py-2 font-mono font-semibold"
                    style={{ color: t.pnl != null ? (t.pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-muted)' }}
                  >
                    {fmt$(t.pnl)}
                  </td>
                  <td className="px-3 py-2" style={{ color: 'var(--text-muted)' }}>
                    {t.holding_days ?? '—'}d
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-muted)' }}>
                    {t.entry_ivr.toFixed(0)}
                  </td>
                  <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-muted)' }}>
                    {t.vix_at_entry != null ? t.vix_at_entry.toFixed(1) : '—'}
                  </td>
                  <td className="px-3 py-2">
                    {t.sma50_position != null ? (
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                        style={{
                          backgroundColor: t.sma50_position === 'above'
                            ? 'color-mix(in srgb, var(--green) 15%, transparent)'
                            : 'color-mix(in srgb, var(--red) 15%, transparent)',
                          color: t.sma50_position === 'above' ? 'var(--green)' : 'var(--red)',
                          border: `1px solid ${t.sma50_position === 'above' ? 'color-mix(in srgb, var(--green) 40%, transparent)' : 'color-mix(in srgb, var(--red) 40%, transparent)'}`,
                        }}
                      >
                        {t.sma50_position}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)' }}>—</span>
                    )}
                  </td>
                  <td className="px-3 py-2" style={{ color: 'var(--text-muted)' }}>
                    {t.entry_regime}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                      style={{
                        backgroundColor: 'color-mix(in srgb, currentColor 10%, transparent)',
                        color: exitReasonColor(t.exit_reason),
                        border: '1px solid currentColor',
                      }}
                    >
                      {exitReasonBadge(t.exit_reason)}
                    </span>
                  </td>
                </tr>
              ))}
              {filteredTrades.length === 0 && (
                <tr>
                  <td colSpan={14} className="px-4 py-8 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
                    No trades match the current filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  positive,
  negative,
}: {
  label: string
  value: string
  positive?: boolean
  negative?: boolean
}) {
  const color = negative ? 'var(--red)' : positive === true ? 'var(--green)' : positive === false ? 'var(--red)' : 'var(--text-primary)'
  return (
    <div
      className="rounded-lg p-3 space-y-1"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
        {label}
      </div>
      <div className="text-lg font-semibold font-mono" style={{ color }}>
        {value}
      </div>
    </div>
  )
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg p-4"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h3>
      {children}
    </div>
  )
}
