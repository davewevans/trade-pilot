import { useEffect, useState } from 'react'
import { api, type SourceHealthEntry } from '../api/client'

type Source = {
  name: string
  tagline: string
  website: string
  cache?: string
  fallback?: string
  /** Key(s) in the source_health.json that map to this source. */
  healthKeys?: string[]
  body: React.ReactNode
}

function Section({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <div className="mt-3">
      <div
        className="text-[10px] uppercase tracking-wider font-semibold mb-1"
        style={{ color: 'var(--text-muted)' }}
      >
        {heading}
      </div>
      <div className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </div>
    </div>
  )
}

function Bullets({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="space-y-1 mt-1">
      {items.map((it, i) => (
        <li key={i} className="flex gap-2 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <span style={{ color: 'var(--accent)' }}>•</span>
          <span>{it}</span>
        </li>
      ))}
    </ul>
  )
}

function SubHead({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-semibold text-sm mt-3 mb-1" style={{ color: 'var(--text-primary)' }}>
      {children}
    </div>
  )
}

const SOURCES: Source[] = [
  {
    name: 'Alpaca',
    tagline: 'Broker, market data, options chain, and news',
    website: 'alpaca.markets',
    healthKeys: ['Alpaca', 'Alpaca News'],
    cache: 'None — all Alpaca data is fetched live each cycle.',
    fallback:
      'If Alpaca is unavailable, the cycle cannot run. The bot logs the error and skips the cycle.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          The bot uses Alpaca for four distinct purposes:
        </p>
        <SubHead>1 — Trading API</SubHead>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Executes all orders (options and stock), retrieves account state (buying power, equity,
          options approval level), fetches current open positions and open orders, and listens for
          assignment events (NTA events that signal a short put was assigned). This is the only
          channel through which trades are placed.
        </p>
        <SubHead>2 — Options Chain</SubHead>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Fetches the full option chain for each watchlist symbol — every available contract with
          its expiration date, strike price, open interest, and close price. Used to identify
          candidate contracts that meet delta and DTE targets.
        </p>
        <SubHead>3 — Options Snapshots</SubHead>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          For contracts that pass the initial chain filter, fetches live snapshots containing the
          real-time bid/ask quote, last trade price, implied volatility, and all five Greeks
          (delta, gamma, theta, vega, rho). This is the data Claude sees when evaluating a specific
          contract.
        </p>
        <SubHead>4 — News</SubHead>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Fetches the five most recent news headlines for each watchlist symbol. Claude receives
          these headlines as part of its context — recent news can signal earnings surprises,
          product announcements, or macro events that affect the trading decision.
        </p>
      </>
    ),
  },
  {
    name: 'ORATS (Options Research & Technology Services)',
    tagline: 'Professional-grade implied volatility analytics',
    website: 'orats.io',
    healthKeys: ['ORATS'],
    cache: '30 minutes per symbol.',
    fallback:
      'If ORATS is unavailable (API key missing or API error), IV rank returns as unavailable and is logged. Strategies that require IV rank will skip rather than proceed without it.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          ORATS is the bot's primary source for implied volatility data. Unlike basic IV
          calculations derived from a single option price, ORATS computes IV analytics across the
          full options surface using institutional-grade models.
        </p>
        <Section heading="What the bot fetches">
          <Bullets
            items={[
              <><strong style={{ color: 'var(--text-primary)' }}>IV Rank (1-year):</strong> Where current IV sits within its 52-week range. 0 = at the yearly low, 100 = at the yearly high. The primary entry filter — most strategies require IVR ≥ 30.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>IV Rank (1-month):</strong> Same calculation over the past 30 days. Useful for detecting recent IV spikes vs. sustained elevation.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>IV Percentile (1-year and 1-month):</strong> The percentage of days in the past year (or month) where IV was lower than today. Different from IV rank — a single spike can distort rank but not percentile.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>ATM IV across four expiration months (M1–M4):</strong> The at-the-money implied volatility for the nearest four monthly expirations. Used to assess the volatility term structure.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>Term Structure Slope:</strong> The difference between M2 and M1 ATM IV. Positive (contango) = normal; negative (backwardation) = near-term fear or event premium.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>Skew (M1 and M2):</strong> The difference in IV between OTM puts and OTM calls. High put skew means downside risk is being priced more aggressively than upside.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>Implied Move %:</strong> ORATS' estimate of the expected price move over the next 30 days based on current options pricing.</>,
              <><strong style={{ color: 'var(--text-primary)' }}>Forecast Move %:</strong> ORATS' model-based forecast of expected move, distinct from the market-implied move.</>,
            ]}
          />
        </Section>
      </>
    ),
  },
  {
    name: 'Finnhub',
    tagline: 'Earnings calendar (primary source)',
    website: 'finnhub.io',
    healthKeys: ['Finnhub'],
    cache: '6 hours per symbol.',
    fallback:
      'If Finnhub is unavailable or returns no data, the bot falls back to yfinance for earnings dates (without EPS/revenue estimates). If both fail, earnings data is marked as unavailable and the cycle proceeds without it — but Claude is told the data is missing.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Finnhub is the bot's primary source for upcoming earnings dates. Knowing when a company
          reports earnings is critical — the bot hard-blocks new positions when earnings are too
          close, because earnings announcements cause unpredictable price gaps that can blow
          through a short option's strike overnight.
        </p>
        <Section heading="What the bot fetches">
          <Bullets
            items={[
              'Next scheduled earnings date',
              'EPS estimate for the upcoming report',
              'Revenue estimate for the upcoming report',
              'Source tag (so the dashboard can show whether data came from Finnhub or the yfinance fallback)',
            ]}
          />
        </Section>
      </>
    ),
  },
  {
    name: 'yfinance',
    tagline: 'Stock technicals, fundamentals, and VIX',
    website: 'pypi.org/project/yfinance',
    healthKeys: ['yfinance'],
    cache:
      'Fundamentals and ex-dividend data cached 6 hours. Technicals computed fresh each cycle. VIX fetched live each cycle.',
    fallback:
      'If yfinance fails for technicals, the context includes None values and Claude is told the data is unavailable. Earnings date fallback from Finnhub handles the most critical field.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          yfinance is a Python library that pulls data from Yahoo Finance. The bot uses it for
          three categories of data that don't require real-time precision.
        </p>
        <SubHead>1 — Stock Technicals (computed from historical price data)</SubHead>
        <Bullets
          items={[
            'Current price and recent price change (5-day, 20-day, 30-day)',
            'RSI-14 (Relative Strength Index — momentum indicator)',
            'SMA-20, SMA-50, SMA-200 (Simple Moving Averages)',
            'Whether the stock is above or below each SMA',
            'Golden cross flag (50-day SMA above 200-day SMA)',
            'Bollinger Bands (upper and lower band based on 20-day SMA ± 2 std deviations)',
            'MACD value, signal line, and bullish/bearish flag',
            'ATR-14 (Average True Range — daily volatility measure)',
            'Average volume (10-day and 30-day) and volume trend',
          ]}
        />
        <SubHead>2 — Fundamentals</SubHead>
        <Bullets
          items={[
            'Next earnings date (fallback when Finnhub is unavailable)',
            'Days until earnings',
            'PE ratio',
            'Market cap',
            'Sector and industry',
            'Average daily volume',
            '52-week high and low',
            'Next ex-dividend date and days until ex-dividend',
            'Annual dividend yield',
          ]}
        />
        <SubHead>3 — VIX</SubHead>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Fetches the current VIX index value (^VIX) directly. This is one of the three primary
          signals used to classify market regime.
        </p>
      </>
    ),
  },
  {
    name: 'FRED (Federal Reserve Economic Data)',
    tagline: 'Risk-free interest rate',
    website: 'fred.stlouisfed.org',
    healthKeys: ['FRED'],
    cache: '4 hours. The rate changes slowly — daily updates from the Fed are sufficient.',
    fallback:
      'If FRED is unavailable, the bot defaults to 5.0% (0.05) and logs a warning. This is a reasonable approximation that avoids blocking a cycle over a slowly-changing macro input.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          FRED is maintained by the Federal Reserve Bank of St. Louis and provides economic data
          series. The bot uses it for one specific data point: the current risk-free interest rate.
        </p>
        <Section heading="What the bot fetches">
          <Bullets items={['The 3-Month Treasury Bill secondary market rate (series: DGS3MO)']} />
        </Section>
        <Section heading="What it is used for">
          The risk-free rate feeds into options pricing models (specifically Black-Scholes) and is
          included in the context Claude receives. It also informs the macro picture — a rising
          risk-free rate affects the relative attractiveness of options premium vs. holding cash.
        </Section>
      </>
    ),
  },
  {
    name: 'CNN Fear & Greed Index',
    tagline: 'Market sentiment score',
    website: 'edition.cnn.com/markets/fear-and-greed',
    healthKeys: ['CNN Fear & Greed'],
    cache: '1 hour.',
    fallback:
      'If the score is unavailable, regime classification proceeds without it. EUPHORIA cannot be triggered without a score, but BULL, NEUTRAL, BEAR, and CRASH can all be determined from VIX and SPX trend alone.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          The Fear &amp; Greed Index is a daily composite sentiment score published by CNN Business.
          It combines seven market signals — stock price momentum, stock price strength, stock price
          breadth, put/call ratio, market volatility, safe haven demand, and junk bond demand — into
          a single number from 0 (Extreme Fear) to 100 (Extreme Greed).
        </p>
        <Section heading="What the bot fetches">
          <Bullets
            items={[
              'The current score (0–100)',
              'The current rating label (Extreme Fear / Fear / Neutral / Greed / Extreme Greed)',
            ]}
          />
        </Section>
        <Section heading="What it is used for">
          One of the three signals used to classify market regime. It is the primary trigger for
          the EUPHORIA regime (score ≥ 75 combined with low VIX and SPX above its 50-day SMA). It
          also influences Claude's assessment of assignment risk and premium-selling conditions.
        </Section>
      </>
    ),
  },
  {
    name: 'Anthropic (Claude API)',
    tagline: 'AI decision engine',
    website: 'anthropic.com',
    cache: 'None — Claude is called fresh every cycle with live data.',
    fallback:
      'If the Anthropic API is unavailable, the cycle logs the error and skips. No trade is placed when Claude cannot be reached.',
    body: (
      <>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Claude is not a passive tool — it is the decision-maker. After all six data sources are
          assembled into a single context package, the full package is sent to Claude with a
          strategy-specific prompt. Claude reads the data, applies the strategy rules, and returns
          a structured JSON recommendation.
        </p>
        <Section heading="What Claude receives">
          The complete context package: account state, open positions, option chain candidates, all
          technicals, fundamentals, macro data (VIX, Fear &amp; Greed, risk-free rate), IV analytics
          from ORATS, earnings dates from Finnhub, news headlines from Alpaca, the current market
          regime and IV environment, and the current wheel state or spread strategy state.
        </Section>
        <Section heading="What Claude returns">
          A structured JSON object specifying: the action (trade/skip/hold/roll), the exact OCC
          symbol if trading, quantity, order type, limit price, and a plain-English reasoning
          string explaining the decision.
        </Section>
        <Section heading="Model">
          <code className="font-mono text-sm">claude-sonnet-4-6</code> (Anthropic)
        </Section>
      </>
    ),
  },
]

type HealthStatus = 'green' | 'yellow' | 'red' | 'gray'

function resolveStatus(
  keys: string[] | undefined,
  sources: Record<string, SourceHealthEntry>,
): HealthStatus {
  if (!keys || keys.length === 0) return 'gray'
  const statuses = keys.map((k) => {
    const e = sources[k]
    if (!e || e.last_checked === null) return 'gray' as HealthStatus
    if (e.consecutive_failures >= 3) return 'red' as HealthStatus
    const lastSuccess = e.last_success ? new Date(e.last_success).getTime() : null
    if (lastSuccess === null) return 'red' as HealthStatus
    const ageMins = (Date.now() - lastSuccess) / 60_000
    if (e.consecutive_failures >= 1 && e.consecutive_failures <= 2) return 'yellow' as HealthStatus
    if (ageMins > 120) return 'red' as HealthStatus
    if (ageMins > 30) return 'yellow' as HealthStatus
    return 'green' as HealthStatus
  })
  // Worst status wins
  if (statuses.includes('red')) return 'red'
  if (statuses.includes('yellow')) return 'yellow'
  if (statuses.includes('green')) return 'green'
  return 'gray'
}

const STATUS_DOT_COLOR: Record<HealthStatus, string> = {
  green: '#3fb950',
  yellow: '#d29922',
  red: '#f85149',
  gray: '#6e7681',
}

function fmtAge(iso: string | null): string {
  if (!iso) return 'never'
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function LiveStatus({
  keys,
  sources,
}: {
  keys: string[] | undefined
  sources: Record<string, SourceHealthEntry>
}) {
  if (!keys) return null
  const status = resolveStatus(keys, sources)

  // Find the best last_success across all keys
  const allEntries = keys.map((k) => sources[k]).filter(Boolean)
  const lastSuccess = allEntries.reduce<string | null>((best, e) => {
    if (!e.last_success) return best
    if (!best) return e.last_success
    return e.last_success > best ? e.last_success : best
  }, null)

  return (
    <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
      <span
        style={{
          display: 'inline-block',
          width: 8,
          height: 8,
          borderRadius: '50%',
          backgroundColor: STATUS_DOT_COLOR[status],
          flexShrink: 0,
        }}
      />
      {status === 'gray'
        ? 'no data yet'
        : lastSuccess
        ? `last success ${fmtAge(lastSuccess)}`
        : 'never succeeded'}
    </div>
  )
}

function SourceCard({
  src,
  idx,
  sources,
}: {
  src: Source
  idx: number
  sources: Record<string, SourceHealthEntry>
}) {
  return (
    <section
      className="rounded-lg border overflow-hidden"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div
        className="px-5 py-4 border-b flex items-baseline justify-between gap-4 flex-wrap"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}
      >
        <div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span
              className="text-[10px] font-mono px-2 py-0.5 rounded"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--accent) 18%, transparent)',
                color: 'var(--accent)',
              }}
            >
              {String(idx).padStart(2, '0')}
            </span>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
              {src.name}
            </h2>
          </div>
          <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
            {src.tagline}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <code className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>
            {src.website}
          </code>
          <LiveStatus keys={src.healthKeys} sources={sources} />
        </div>
      </div>
      <div className="p-5">
        {src.body}
        {(src.cache || src.fallback) && (
          <div
            className="mt-5 pt-4 border-t grid gap-3 md:grid-cols-2"
            style={{ borderColor: 'var(--border)' }}
          >
            {src.cache && (
              <div>
                <div
                  className="text-[10px] uppercase tracking-wider font-semibold mb-1"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Cache
                </div>
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                  {src.cache}
                </p>
              </div>
            )}
            {src.fallback && (
              <div>
                <div
                  className="text-[10px] uppercase tracking-wider font-semibold mb-1"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Fallback
                </div>
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                  {src.fallback}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

const FALLBACK_CHAIN = [
  { label: 'Earnings date', chain: 'Finnhub → yfinance → marked unavailable' },
  { label: 'IV Rank', chain: 'ORATS → marked unavailable (strategies skip without it)' },
  { label: 'Risk-free rate', chain: 'FRED → 5.0% default' },
  { label: 'VIX', chain: 'yfinance → None (regime defaults to NEUTRAL)' },
  { label: 'Fear & Greed', chain: 'CNN → None (EUPHORIA regime blocked, others unaffected)' },
  { label: 'Technicals', chain: 'yfinance → None values passed to Claude with warning' },
  { label: 'Alpaca', chain: 'No fallback — cycle cannot run without the broker connection' },
]

export function DataSources() {
  const [healthSources, setHealthSources] = useState<Record<string, SourceHealthEntry>>({})

  useEffect(() => {
    api.sourceHealth().then((d) => setHealthSources(d.sources)).catch(() => {})
  }, [])

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          Data Sources
        </h1>
        <p className="text-sm mt-2 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Every decision cycle, the bot assembles a context package from seven data sources before
          asking Claude anything. The quality of that context directly determines the quality of
          the decisions. This page explains what each source provides, how fresh the data is, and
          what happens when a source is unavailable.
        </p>
      </div>

      <div className="space-y-5">
        {SOURCES.map((s, i) => (
          <SourceCard key={s.name} src={s} idx={i + 1} sources={healthSources} />
        ))}
      </div>

      {/* Fallback chain summary */}
      <section className="mt-10">
        <h2
          className="text-xs uppercase tracking-wider font-semibold mb-3"
          style={{ color: 'var(--text-muted)' }}
        >
          Fallback Chains
        </h2>
        <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
          When a data source is unavailable, the bot degrades gracefully rather than crashing or
          proceeding with stale data:
        </p>
        <div
          className="rounded-lg border divide-y"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          {FALLBACK_CHAIN.map((row) => (
            <div
              key={row.label}
              className="px-4 py-3 grid gap-1 md:gap-4 md:grid-cols-[12rem_1fr]"
              style={{ borderColor: 'var(--border)' }}
            >
              <div
                className="font-semibold text-sm"
                style={{ color: 'var(--text-primary)' }}
              >
                {row.label}
              </div>
              <div className="text-sm font-mono" style={{ color: 'var(--text-secondary)' }}>
                {row.chain}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
