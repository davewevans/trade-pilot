import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CoverageStats } from '../components/research/CoverageStats'
import { LiquidityHeatmap } from '../components/research/LiquidityHeatmap'
import { WinRateHeatmap } from '../components/research/WinRateHeatmap'
import { CombinedHeatmap } from '../components/research/CombinedHeatmap'
import { SymbolDeepDive } from '../components/research/SymbolDeepDive'
import { RecommendationScorecard } from '../components/research/RecommendationScorecard'
import { StalenessBadge } from '../components/shared/StalenessBadge'
import { api, type ResearchLastRunResponse } from '../api/client'

// ── Types ─────────────────────────────────────────────────────────────────────

interface LiqScore {
  symbol: string
  strategy_type: string
  tier: string
  confidence: string
  composite_score: number | null
}

// ── Last sweep status badge ──────────────────────────────────────────────────

function LastSweepBadge({ lastRun }: { lastRun: ResearchLastRunResponse }) {
  const { status, last_run_at, detail } = lastRun

  if (status === 'aborted') {
    return (
      <span
        className="text-xs px-2 py-0.5 rounded"
        style={{ backgroundColor: 'var(--red)', color: 'white' }}
        title={detail ?? undefined}
      >
        Last sweep: aborted — ORATS budget
      </span>
    )
  }

  if (status === 'ok' && last_run_at) {
    const diffMs = Date.now() - new Date(last_run_at).getTime()
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
    const when = diffDays <= 0 ? 'today' : `${diffDays}d ago`
    return (
      <span className="text-xs" style={{ color: 'var(--text-muted)' }} title={last_run_at}>
        Last sweep: {when}
      </span>
    )
  }

  return (
    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
      Last sweep run: never
    </span>
  )
}

// ── Tab config ────────────────────────────────────────────────────────────────

type Tab = 'liquidity' | 'winrate' | 'combined' | 'scorecard'

const TABS: { key: Tab; label: string; description: string }[] = [
  {
    key: 'liquidity',
    label: 'Liquidity',
    description: 'Option market quality tiers — bid-ask spread, open interest, and volume',
  },
  {
    key: 'winrate',
    label: 'Win Rate',
    description: 'Historical backtest win rates per (symbol, strategy) pair',
  },
  {
    key: 'combined',
    label: 'Combined',
    description: 'Effective total score multiplier = liquidity × win-rate',
  },
  {
    key: 'scorecard',
    label: 'Scorecard',
    description: 'Recommendation accuracy — how often did accepted/rejected decisions turn out to be right?',
  },
]

// ── Component ─────────────────────────────────────────────────────────────────

export function Research() {
  const [activeTab, setActiveTab] = useState<Tab>('winrate')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [liquidityScores, setLiquidityScores] = useState<LiqScore[]>([])
  const [pendingRecs, setPendingRecs] = useState<number>(0)
  const [lastRun, setLastRun] = useState<ResearchLastRunResponse | null>(null)

  // Fetch pending recommendation count for the nudge card
  useEffect(() => {
    fetch('/api/research/recommendations', { credentials: 'same-origin' })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (!d) return
        let pending = 0
        for (const wl of Object.values(d.watchlists as Record<string, { add: unknown[]; remove: unknown[] }>)) {
          pending += (wl.add?.length ?? 0) + (wl.remove?.length ?? 0)
        }
        setPendingRecs(pending)
      })
      .catch(() => {})
  }, [])

  // Fetch last-run staleness info
  useEffect(() => {
    api.researchLastRun()
      .then((d) => setLastRun(d))
      .catch(() => {})
  }, [])

  // Pre-fetch liquidity scores so SymbolDeepDive can show them without a
  // separate round-trip (it already has the data from the heatmap render).
  useEffect(() => {
    fetch('/api/research/liquidity/scores', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return
        const all: LiqScore[] = []
        for (const rows of Object.values(d.strategies as Record<string, LiqScore[]>)) {
          all.push(...rows)
        }
        setLiquidityScores(all)
      })
      .catch(() => {})
  }, [])

  const activeTabMeta = TABS.find((t) => t.key === activeTab)!

  return (
    <div className="p-5 max-w-screen-xl mx-auto">
      {/* Page header */}
      <div className="mb-5">
        <h1 className="text-2xl font-bold mb-1" style={{ color: 'var(--text-primary)' }}>
          Research
        </h1>
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          Automated research scores driving score multipliers in live strategy decisions.
        </p>
      </div>

      {/* Pending recommendations nudge */}
      {pendingRecs > 0 && (
        <Link
          to="/recommendations"
          style={{ textDecoration: 'none' }}
        >
          <div
            className="mb-4 rounded-lg px-4 py-3 flex items-center justify-between"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--accent) 12%, var(--bg-card))',
              border: '1px solid color-mix(in srgb, var(--accent) 35%, var(--border))',
              cursor: 'pointer',
            }}
          >
            <span style={{ fontSize: 14, color: 'var(--text-primary)', fontWeight: 600 }}>
              {pendingRecs} pending watchlist recommendation{pendingRecs !== 1 ? 's' : ''}
            </span>
            <span style={{ fontSize: 13, color: 'var(--accent)', fontWeight: 600 }}>
              Review →
            </span>
          </div>
        </Link>
      )}

      {/* Coverage stats */}
      <div className="flex items-center justify-between flex-wrap gap-2 mb-1">
        <div className="flex-1">
          <CoverageStats />
        </div>
        {lastRun && (
          <div className="flex gap-4 text-xs shrink-0">
            <LastSweepBadge lastRun={lastRun} />
            <StalenessBadge last_updated={lastRun.most_recent_scores} label="scan" />
          </div>
        )}
      </div>

      {/* Tab selector */}
      <div
        className="flex gap-1 mb-4 p-1 rounded-lg"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', display: 'inline-flex' }}
      >
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className="px-4 py-2 rounded-md text-sm font-medium transition-all"
            style={{
              backgroundColor: activeTab === tab.key ? 'var(--accent)' : 'transparent',
              color: activeTab === tab.key ? '#fff' : 'var(--text-secondary)',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab description */}
      <p className="mb-4 text-xs" style={{ color: 'var(--text-muted)' }}>
        {activeTabMeta.description}
      </p>

      {/* Main content card */}
      <div
        className="rounded-lg p-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        {activeTab === 'liquidity' && (
          <LiquidityHeatmap onSymbolClick={setSelectedSymbol} />
        )}
        {activeTab === 'winrate' && (
          <WinRateHeatmap onSymbolClick={setSelectedSymbol} />
        )}
        {activeTab === 'combined' && (
          <CombinedHeatmap onSymbolClick={setSelectedSymbol} />
        )}
        {activeTab === 'scorecard' && (
          <RecommendationScorecard lastRun={lastRun} />
        )}
      </div>

      {/* Symbol deep dive drawer */}
      {selectedSymbol && (
        <SymbolDeepDive
          symbol={selectedSymbol}
          onClose={() => setSelectedSymbol(null)}
          liquidityScores={liquidityScores}
        />
      )}
    </div>
  )
}
