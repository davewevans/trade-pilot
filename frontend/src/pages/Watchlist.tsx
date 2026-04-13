import { useEffect, useState } from 'react'
import { api } from '../api/client'

const SYMBOL_SECTORS: Record<string, string> = {
  AAPL: 'Technology', MSFT: 'Technology', GOOGL: 'Technology', AMZN: 'Technology',
  META: 'Technology', NVDA: 'Technology', AMD: 'Technology', TSLA: 'Technology',
  CRM: 'Technology', ORCL: 'Technology', ADBE: 'Technology',
  JPM: 'Financials', GS: 'Financials', BAC: 'Financials', MS: 'Financials', C: 'Financials',
  XOM: 'Energy', CVX: 'Energy', SLB: 'Energy',
  JNJ: 'Healthcare', UNH: 'Healthcare', PFE: 'Healthcare', ABBV: 'Healthcare',
  BA: 'Industrials', CAT: 'Industrials', DE: 'Industrials', GE: 'Industrials',
  DIS: 'Consumer', NFLX: 'Consumer', HD: 'Consumer', LOW: 'Consumer',
  COST: 'Consumer', NKE: 'Consumer', SBUX: 'Consumer',
  SPY: 'Index ETF', QQQ: 'Index ETF', IWM: 'Index ETF', DIA: 'Index ETF',
}

const QUICK_ADD_GROUPS: { label: string; symbols: string[] }[] = [
  { label: 'Tech', symbols: ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'AMD', 'TSLA', 'CRM', 'ORCL'] },
  { label: 'Financials', symbols: ['JPM', 'GS', 'BAC', 'MS', 'C'] },
  { label: 'Energy', symbols: ['XOM', 'CVX', 'SLB'] },
  { label: 'Healthcare', symbols: ['JNJ', 'UNH', 'PFE', 'ABBV'] },
  { label: 'Industrials', symbols: ['BA', 'CAT', 'DE', 'GE'] },
  { label: 'Consumer', symbols: ['DIS', 'NFLX', 'HD', 'COST', 'NKE', 'SBUX'] },
  { label: 'Index ETFs', symbols: ['SPY', 'QQQ', 'IWM', 'DIA'] },
]

function SymbolChip({
  symbol,
  onRemove,
}: {
  symbol: string
  onRemove: (s: string) => void
}) {
  const sector = SYMBOL_SECTORS[symbol]
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--accent) 12%, var(--bg-secondary))',
        border: '1px solid color-mix(in srgb, var(--accent) 30%, var(--border))',
        color: 'var(--text-primary)',
      }}
    >
      <span className="font-semibold">{symbol}</span>
      {sector && (
        <span style={{ color: 'var(--text-muted)' }}>· {sector}</span>
      )}
      <button
        onClick={() => onRemove(symbol)}
        className="ml-1 rounded-full w-3.5 h-3.5 flex items-center justify-center text-[10px] leading-none"
        style={{
          color: 'var(--text-muted)',
          backgroundColor: 'transparent',
        }}
        aria-label={`Remove ${symbol}`}
      >
        ×
      </button>
    </span>
  )
}

function WatchlistSection({
  title,
  note,
  symbols,
  suggestedGroups,
  onChange,
}: {
  title: string
  note: string
  symbols: string[]
  suggestedGroups: { label: string; symbols: string[] }[]
  onChange: (next: string[]) => void
}) {
  const [input, setInput] = useState('')
  const [inputError, setInputError] = useState('')

  const add = (sym: string) => {
    const s = sym.trim().toUpperCase()
    if (!s) return
    if (!/^[A-Z]{1,6}$/.test(s)) {
      setInputError(`"${s}" is not a valid ticker (letters only, max 6)`)
      return
    }
    if (symbols.includes(s)) {
      setInputError(`${s} is already in the list`)
      return
    }
    setInputError('')
    onChange([...symbols, s])
    setInput('')
  }

  const remove = (sym: string) => {
    onChange(symbols.filter((s) => s !== sym))
  }

  return (
    <div
      className="rounded-md p-5 flex flex-col gap-4"
      style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
    >
      <div>
        <h3 className="text-base font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
          {title}
        </h3>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{note}</p>
      </div>

      <div className="flex flex-wrap gap-2 min-h-[2rem]">
        {symbols.length === 0 && (
          <span className="text-xs italic" style={{ color: 'var(--text-muted)' }}>
            No symbols — add some below
          </span>
        )}
        {symbols.map((s) => (
          <SymbolChip key={s} symbol={s} onRemove={remove} />
        ))}
      </div>

      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => { setInput(e.target.value.toUpperCase()); setInputError('') }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(input) } }}
          placeholder="Add ticker (e.g. NVDA)"
          maxLength={6}
          className="flex-1 rounded px-3 py-1.5 text-sm font-mono"
          style={{
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />
        <button
          onClick={() => add(input)}
          className="px-3 py-1.5 rounded text-sm font-medium"
          style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
        >
          Add
        </button>
      </div>
      {inputError && (
        <p className="text-xs" style={{ color: 'var(--red)' }}>{inputError}</p>
      )}

      <div>
        <p className="text-[10px] uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>
          Quick add
        </p>
        <div className="flex flex-col gap-2">
          {suggestedGroups.map((group) => (
            <div key={group.label} className="flex flex-wrap items-center gap-1">
              <span
                className="text-[10px] w-20 shrink-0"
                style={{ color: 'var(--text-muted)' }}
              >
                {group.label}
              </span>
              {group.symbols.map((s) => {
                const already = symbols.includes(s)
                return (
                  <button
                    key={s}
                    onClick={() => !already && add(s)}
                    disabled={already}
                    className="px-2 py-0.5 rounded text-xs font-mono transition-opacity"
                    style={{
                      backgroundColor: already
                        ? 'color-mix(in srgb, var(--accent) 8%, var(--bg-secondary))'
                        : 'var(--bg-secondary)',
                      border: `1px solid ${already ? 'var(--accent)' : 'var(--border)'}`,
                      color: already ? 'var(--accent)' : 'var(--text-secondary)',
                      opacity: already ? 0.6 : 1,
                      cursor: already ? 'default' : 'pointer',
                    }}
                  >
                    {s}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function Watchlist() {
  const [wheel, setWheel] = useState<string[]>([])
  const [spreads, setSpreads] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.watchlist()
      .then((data) => {
        setWheel(data.wheel)
        setSpreads(data.spreads)
        setSavedAt(data.updated_at)
      })
      .catch(() => setError('Failed to load watchlist'))
      .finally(() => setLoading(false))
  }, [])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const data = await api.updateWatchlist(wheel, spreads)
      setSavedAt(data.updated_at)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40" style={{ color: 'var(--text-muted)' }}>
        Loading…
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h2 className="section-heading">Watchlist</h2>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          Changes take effect on the next scheduler cycle — no restart needed.
          {savedAt && (
            <span style={{ color: 'var(--text-muted)' }}>
              {' '}Last saved {new Date(savedAt).toLocaleString()}.
            </span>
          )}
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <WatchlistSection
          title="Wheel Watchlist"
          note="Stocks the bot might buy 100 shares of through the wheel strategy. Only add stocks you've researched and would be comfortable holding."
          symbols={wheel}
          suggestedGroups={QUICK_ADD_GROUPS}
          onChange={setWheel}
        />
        <WatchlistSection
          title="Spread Watchlist"
          note="The bot scans these for spread opportunities. You never own the stock — just the spread. Add any stock with a liquid options chain."
          symbols={spreads}
          suggestedGroups={QUICK_ADD_GROUPS}
          onChange={setSpreads}
        />
      </div>

      {error && (
        <div
          className="rounded px-4 py-2 text-sm"
          style={{ backgroundColor: 'color-mix(in srgb, var(--red) 12%, var(--bg-card))', color: 'var(--red)', border: '1px solid color-mix(in srgb, var(--red) 30%, var(--border))' }}
        >
          {error}
        </div>
      )}

      <div className="flex items-center gap-4">
        <button
          onClick={save}
          disabled={saving}
          className="px-5 py-2 rounded font-medium text-sm"
          style={{
            backgroundColor: saving ? 'var(--text-muted)' : 'var(--accent)',
            color: '#fff',
            cursor: saving ? 'default' : 'pointer',
          }}
        >
          {saving ? 'Saving…' : 'Save watchlist'}
        </button>
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {wheel.length} wheel · {spreads.length} spreads
        </span>
      </div>
    </div>
  )
}
