/**
 * Volatility dashboard — IV rank history for any symbol.
 *
 * The primary use case: "AAPL has been below IVR 30 for 6 weeks —
 * that's why the bot keeps skipping it."
 */

import { useEffect, useRef, useState } from 'react'
import { IVHistoryChart } from '../components/shared/IVHistoryChart'
import { api } from '../api/client'

// Preset symbol groups for quick access
const PRESET_GROUPS: { label: string; symbols: string[] }[] = [
  {
    label: 'Index ETFs',
    symbols: ['SPY', 'QQQ', 'IWM', 'DIA', 'GLD', 'TLT'],
  },
  {
    label: 'Tech',
    symbols: ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA'],
  },
  {
    label: 'Financials',
    symbols: ['JPM', 'BAC', 'GS', 'MS', 'C', 'WFC'],
  },
]

const ALL_PRESETS = PRESET_GROUPS.flatMap((g) => g.symbols)

export function Volatility() {
  const [symbol, setSymbol] = useState('SPY')
  const [inputValue, setInputValue] = useState('SPY')
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  // Load watchlist symbols to show as quick picks
  useEffect(() => {
    api.watchlist().then((wl) => {
      const all = [...wl.wheel, ...wl.iron_condor, ...wl.spreads]
      const unique = Array.from(new Set(all)).filter(
        (s) => !ALL_PRESETS.includes(s),
      )
      setWatchlistSymbols(unique.slice(0, 12))
    }).catch(() => {})
  }, [])

  function handleInputSubmit(e: React.FormEvent) {
    e.preventDefault()
    const sym = inputValue.trim().toUpperCase()
    if (sym) {
      setSymbol(sym)
      setInputValue(sym)
    }
  }

  function selectPreset(sym: string) {
    setSymbol(sym)
    setInputValue(sym)
    inputRef.current?.blur()
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Volatility</h2>
        <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
          IV rank history — see why the bot is entering or skipping each symbol.
        </p>
      </div>

      {/* ── Symbol picker ──────────────────────────────────────────────────── */}
      <div
        className="rounded-lg p-4 space-y-3"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <form onSubmit={handleInputSubmit} className="flex gap-2">
          <input
            ref={inputRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value.toUpperCase())}
            placeholder="Enter symbol…"
            maxLength={10}
            className="flex-1 px-3 py-1.5 rounded text-sm font-mono"
            style={{
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
              outline: 'none',
            }}
          />
          <button
            type="submit"
            className="px-4 py-1.5 rounded text-sm font-medium"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg-primary)' }}
          >
            View
          </button>
        </form>

        {PRESET_GROUPS.map((group) => (
          <div key={group.label} className="flex items-center gap-2 flex-wrap">
            <span className="text-xs w-20 shrink-0" style={{ color: 'var(--text-muted)' }}>
              {group.label}
            </span>
            {group.symbols.map((sym) => (
              <button
                key={sym}
                onClick={() => selectPreset(sym)}
                className="px-2 py-0.5 rounded text-xs font-mono transition-colors"
                style={{
                  backgroundColor:
                    symbol === sym
                      ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
                      : 'var(--bg-secondary)',
                  color: symbol === sym ? 'var(--accent)' : 'var(--text-secondary)',
                  border: `1px solid ${symbol === sym ? 'var(--accent)' : 'var(--border)'}`,
                }}
              >
                {sym}
              </button>
            ))}
          </div>
        ))}

        {watchlistSymbols.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs w-20 shrink-0" style={{ color: 'var(--text-muted)' }}>
              Watchlist
            </span>
            {watchlistSymbols.map((sym) => (
              <button
                key={sym}
                onClick={() => selectPreset(sym)}
                className="px-2 py-0.5 rounded text-xs font-mono transition-colors"
                style={{
                  backgroundColor:
                    symbol === sym
                      ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-card))'
                      : 'var(--bg-secondary)',
                  color: symbol === sym ? 'var(--accent)' : 'var(--text-secondary)',
                  border: `1px solid ${symbol === sym ? 'var(--accent)' : 'var(--border)'}`,
                }}
              >
                {sym}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Chart ──────────────────────────────────────────────────────────── */}
      {symbol && (
        <div
          className="rounded-lg p-4"
          style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
        >
          <IVHistoryChart symbol={symbol} />
        </div>
      )}

      {/* ── Explainer ──────────────────────────────────────────────────────── */}
      <div
        className="rounded-lg p-4 text-sm space-y-2"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
      >
        <div className="font-semibold" style={{ color: 'var(--text-primary)' }}>
          How to read this chart
        </div>
        <ul className="space-y-1.5 list-disc list-inside" style={{ color: 'var(--text-muted)' }}>
          <li>
            <strong style={{ color: 'var(--text-secondary)' }}>IV Rank (1y)</strong> — where today's
            IV sits within the past year. 0 = lowest IV seen, 100 = highest.
          </li>
          <li>
            <strong style={{ color: '#22c55e' }}>Above 30 (green)</strong> — the bot's entry
            threshold. When IVR is here, premium is rich enough to sell.
          </li>
          <li>
            <strong style={{ color: '#ef4444' }}>Below 30 (red)</strong> — premium is too cheap.
            The bot skips entries; the pre-checks reject any opportunity.
          </li>
          <li>
            <strong style={{ color: 'var(--accent)' }}>Dots</strong> — bot trade entries. Green dot
            = entered at good IV level (≥ 30). Red dot = entered below threshold (unusual, may
            indicate override or IVR data lag).
          </li>
          <li>
            <strong style={{ color: 'var(--text-secondary)' }}>High IV line (50)</strong> — above
            this, IV is elevated. Iron condor and high-credit CSP setups become more attractive.
          </li>
        </ul>
      </div>
    </div>
  )
}
