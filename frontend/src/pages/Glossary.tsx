import { useMemo, useState } from 'react'

type Term = { term: string; def: string }
type Section = { title: string; terms: Term[] }

const SECTIONS: Section[] = [
  {
    title: 'Options Basics',
    terms: [
      { term: 'Option', def: 'A contract that gives the buyer the right, but not the obligation, to buy or sell 100 shares of a stock at a specific price before a specific date. The seller collects a premium and takes on the obligation.' },
      { term: 'Call Option', def: 'The right to buy 100 shares at the strike price. Buyers profit when the stock rises above the strike. Sellers (like this bot) profit when the stock stays below the strike.' },
      { term: 'Put Option', def: 'The right to sell 100 shares at the strike price. Buyers profit when the stock falls below the strike. Sellers profit when the stock stays above the strike.' },
      { term: 'Strike Price', def: "The price at which the option contract can be exercised. For a put, it's the price the buyer can force a sale at. For a call, it's the price the buyer can force a purchase at." },
      { term: 'Expiration Date', def: 'The date the option contract expires. After this date, the option is worthless if not exercised.' },
      { term: 'DTE (Days to Expiration)', def: 'The number of calendar days remaining until the option expires. The bot targets 21–35 DTE for most strategies.' },
      { term: 'Premium', def: 'The price paid by the option buyer and collected by the option seller. This is the income the bot earns when it sells options.' },
      { term: 'ITM (In the Money)', def: 'An option that has intrinsic value. A put is ITM when the stock price is below the strike. A call is ITM when the stock price is above the strike. ITM options are at risk of assignment.' },
      { term: 'OTM (Out of the Money)', def: 'An option with no intrinsic value, only time value. A put is OTM when the stock price is above the strike. A call is OTM when the stock price is below the strike. The bot sells OTM options intentionally.' },
      { term: 'ATM (At the Money)', def: 'An option whose strike price is approximately equal to the current stock price. ATM options have the highest time value and the fastest theta decay.' },
      { term: 'Intrinsic Value', def: 'The real, tangible value of an option if exercised right now. Only ITM options have intrinsic value.' },
      { term: 'Extrinsic Value', def: "Also called time value. The portion of an option's price above its intrinsic value, driven by time remaining and implied volatility. This is what decays to zero by expiration, and what the bot profits from as the seller." },
      { term: 'Open Interest', def: 'The total number of outstanding option contracts that have not been settled. Higher open interest means more liquidity. The bot requires open interest ≥ 200 for wheel trades.' },
      { term: 'Bid-Ask Spread', def: 'The difference between the highest price a buyer will pay (bid) and the lowest price a seller will accept (ask). Wide spreads mean poor liquidity and worse fills. The bot avoids contracts with spreads wider than $0.15.' },
      { term: 'Assignment', def: 'When an option seller is obligated to fulfill the contract. For a short put: obligated to buy 100 shares at the strike. For a short call: obligated to sell 100 shares at the strike. Assignment typically happens when an option expires ITM.' },
      { term: 'Exercise', def: 'When the option buyer chooses to use their right to buy or sell shares. Assignment is what happens to the seller when the buyer exercises.' },
      { term: 'OCC Symbol', def: 'The standardized format for identifying an option contract: ROOT + YYMMDD + C/P + 8-digit strike. Example: AAPL260501P00250000 = Apple / May 1 2026 / Put / $250 strike.' },
    ],
  },
  {
    title: 'The Greeks',
    terms: [
      { term: 'Delta', def: "How much the option's price changes for every $1 move in the stock. Ranges from 0 to 1 for calls, -1 to 0 for puts. Also approximates the probability of the option expiring ITM. The bot targets delta -0.20 to -0.30 for cash-secured puts (roughly 20–30% chance of assignment, 70–80% probability of profit)." },
      { term: 'Theta', def: 'The daily dollar amount an option loses in value due to time passing, all else equal. Always positive for option sellers — the bot collects theta every day it holds a short position. Theta accelerates as expiration approaches, which is why 21–35 DTE is the sweet spot.' },
      { term: 'Vega', def: "How much the option's price changes for a 1% change in implied volatility. Option sellers are short vega: if IV drops after selling, the position profits. If IV spikes, the position loses. This is why the bot sells when IV is already elevated." },
      { term: 'Gamma', def: 'The rate at which delta changes as the stock price moves. High gamma means delta can shift rapidly — a small price move creates a large delta change. Gamma risk is highest near expiration and near the strike price. This is why the bot avoids holding positions with fewer than 7 DTE.' },
      { term: 'Rho', def: "How much the option's price changes with a 1% change in interest rates. Least impactful of the major Greeks for short-term options. The bot monitors the risk-free rate from FRED but rho is not a primary decision factor." },
      { term: 'IV (Implied Volatility)', def: "The market's forward-looking expectation of how much a stock will move, derived from current option prices. Higher IV means more expensive options. The bot sells when IV is elevated so it collects more premium." },
      { term: 'IV Rank (IVR)', def: 'Where current IV sits within its 52-week range, expressed as 0–100. IVR of 0 means IV is at its yearly low. IVR of 100 means IV is at its yearly high. Formula: (Current IV − 52wk Low) ÷ (52wk High − 52wk Low) × 100. The bot requires IVR ≥ 30 for most strategies, ≥ 40 for iron condors.' },
      { term: 'HV (Historical Volatility)', def: 'Realized past price movement, calculated from actual price changes. When IV > HV, options are relatively expensive — a good environment for sellers.' },
    ],
  },
  {
    title: 'Strategy Terms',
    terms: [
      { term: 'CSP (Cash-Secured Put)', def: 'Selling a put option while holding enough cash to buy 100 shares if assigned. The cash acts as collateral. The bot sells CSPs in the IDLE phase of the wheel.' },
      { term: 'CC (Covered Call)', def: 'Selling a call option against 100 shares already owned. The shares act as collateral. The bot sells CCs after being assigned stock from a CSP.' },
      { term: 'Wheel Strategy', def: 'A three-phase income strategy: sell a CSP → if assigned, own the stock → sell a CC → if called away, repeat. The wheel cycles through IDLE → SHORT_PUT → LONG_STOCK → SHORT_CALL and back to IDLE.' },
      { term: 'Credit Spread', def: 'Selling a closer-to-the-money option and buying a further-away option for protection, collecting a net premium (credit). Examples: bull put spread, bear call spread, iron condor. The long option limits maximum loss.' },
      { term: 'Debit Spread', def: 'Buying a closer-to-the-money option and selling a further-away option, paying a net premium (debit). Example: long call vertical. Used when options are cheap and you want directional exposure with limited risk.' },
      { term: 'Bull Put Spread', def: 'A credit spread using puts. Sell an OTM put and buy a lower-strike put for protection. Profits when the stock stays above the short put strike. Used in neutral-to-bullish conditions.' },
      { term: 'Bear Call Spread', def: 'A credit spread using calls. Sell an OTM call and buy a higher-strike call for protection. Profits when the stock stays below the short call strike. Used in bearish conditions.' },
      { term: 'Iron Condor', def: 'Combining a bull put spread and a bear call spread on the same underlying and expiration. Profits when the stock stays within the range between the two short strikes. Used in neutral, high-IV markets.' },
      { term: 'Long Call Vertical', def: 'A debit spread using calls. Buy a lower-strike call and sell a higher-strike call. Profits when the stock rises above the long strike. The only debit strategy the bot runs, used in bullish, low-IV conditions.' },
      { term: 'Roll', def: 'Closing an existing option position and opening a new one with a different strike, expiration, or both — usually for a net credit. The bot rolls positions that are moving against it to extend time or improve the strike.' },
      { term: 'Max Profit', def: "The most a strategy can earn. For credit strategies, it's the premium collected. Achieved when the option expires worthless." },
      { term: 'Max Loss', def: "The most a strategy can lose. For defined-risk spreads, it's the spread width minus the credit collected. For the wheel's CSP, it's theoretically the full strike price (stock going to zero)." },
      { term: 'Break-Even', def: 'The stock price at expiration where the trade is exactly flat — neither a profit nor a loss.' },
      { term: 'Credit', def: 'Money received when opening a position (selling options). Positive cash flow at entry.' },
      { term: 'Debit', def: 'Money paid when opening a position (buying options or buying back a short). Negative cash flow at entry.' },
    ],
  },
  {
    title: 'Bot-Specific Terms',
    terms: [
      { term: 'Wheel State', def: 'The current phase of the wheel strategy for a given stock: IDLE (looking for a CSP to sell), SHORT_PUT (managing an open CSP), LONG_STOCK (holding assigned shares), or SHORT_CALL (managing an open CC).' },
      { term: 'Market Regime', def: "The bot's classification of current market conditions: BULL, NEUTRAL, BEAR, CRASH, or EUPHORIA. Determined each cycle from VIX, SPX trend, and Fear & Greed. Controls which strategies are allowed to open new positions." },
      { term: 'IV Environment', def: "The bot's classification of implied volatility conditions across the watchlist: LOW (median IVR < 30), MODERATE (30–50), or HIGH (> 50). Used alongside regime to route spread strategy selection." },
      { term: 'Circuit Breaker', def: "A portfolio-level loss control that reduces or halts all new position entry when daily, weekly, or drawdown losses exceed defined thresholds. Independent of Claude and the per-trade guardrails." },
      { term: 'Guardrail', def: "A hard rule enforced in Python code that validates every trade Claude recommends before execution. Guardrails cannot be overridden by Claude's reasoning." },
      { term: 'Stability Filter', def: 'A mechanism that requires 3 consecutive identical regime readings before confirming a regime change. Prevents the bot from switching strategies on a single volatile day.' },
      { term: 'Spread Tracker', def: 'The component that tracks all open spread positions (iron condor, bull put, bear call, long call vertical) across accounts. Used by guardrails to enforce the one-position-per-symbol rule.' },
      { term: 'State Writer', def: 'The component that writes portfolio, context, circuit breaker, and decision snapshots to disk after each cycle. The dashboard reads these snapshot files.' },
      { term: 'NTA (Non-Trade Activity)', def: 'An Alpaca event type indicating that shares were assigned or options expired without a trade. The bot listens for NTA events to detect assignment and transition the wheel state from SHORT_PUT to LONG_STOCK.' },
      { term: 'DRY_RUN', def: 'A mode in which the bot runs its full decision cycle, calls Claude, validates with guardrails, but does not place any actual orders. Used for testing.' },
      { term: 'ATR (Average True Range)', def: "A measure of a stock's average daily price range over a given period (usually 14 days). The bot uses ATR as a sanity check on strike placement — a strike should ideally be at least 1 ATR below the current price." },
      { term: 'SMA (Simple Moving Average)', def: 'The average closing price over a rolling window of N days. The bot uses the 50-day and 200-day SMAs of the S&P 500 to classify market regime, and individual stock SMAs to assess trend health.' },
      { term: 'Golden Cross', def: "When a stock's 50-day SMA crosses above its 200-day SMA. Considered a bullish signal. The bot notes this in context for Claude to factor into its decision." },
      { term: 'Fear & Greed Index', def: 'A 0–100 composite sentiment score published daily. Combines seven market signals into a single number. The bot uses this as one of three inputs to classify market regime. Scores near 0 indicate extreme fear; near 100 indicate extreme greed.' },
      { term: 'VIX', def: "The CBOE Volatility Index. Measures the market's 30-day implied volatility expectation for the S&P 500. Often called the \"fear gauge.\" The bot's primary regime signal: VIX ≥ 35 triggers CRASH regardless of other indicators." },
    ],
  },
]

function highlight(text: string, query: string): React.ReactNode {
  if (!query) return text
  const lower = text.toLowerCase()
  const q = query.toLowerCase()
  const out: React.ReactNode[] = []
  let i = 0
  while (i < text.length) {
    const idx = lower.indexOf(q, i)
    if (idx === -1) {
      out.push(text.slice(i))
      break
    }
    if (idx > i) out.push(text.slice(i, idx))
    out.push(
      <mark
        key={idx}
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 30%, transparent)',
          color: 'var(--text-primary)',
          padding: '0 2px',
          borderRadius: 2,
        }}
      >
        {text.slice(idx, idx + q.length)}
      </mark>,
    )
    i = idx + q.length
  }
  return out
}

export function Glossary() {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()

  const filtered = useMemo(() => {
    if (!q) return SECTIONS
    return SECTIONS.map((s) => ({
      ...s,
      terms: s.terms.filter(
        (t) => t.term.toLowerCase().includes(q) || t.def.toLowerCase().includes(q),
      ),
    })).filter((s) => s.terms.length > 0)
  }, [q])

  const totalMatches = filtered.reduce((n, s) => n + s.terms.length, 0)

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          Glossary
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          Reference for terms used throughout the dashboard, decisions, and trade journal.
        </p>
      </div>

      <div className="mb-6">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search terms or definitions…"
          className="w-full px-4 py-2.5 rounded-lg text-sm outline-none transition-colors"
          style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
        />
        {q && (
          <div className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
            {totalMatches} match{totalMatches === 1 ? '' : 'es'}
          </div>
        )}
      </div>

      {filtered.length === 0 && (
        <div
          className="p-6 rounded-lg border text-center text-sm"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          No terms match “{query}”.
        </div>
      )}

      {filtered.map((section) => (
        <section key={section.title} className="mb-8">
          <h2
            className="text-xs uppercase tracking-wider font-semibold mb-3"
            style={{ color: 'var(--text-muted)' }}
          >
            {section.title}
          </h2>
          <div
            className="rounded-lg border divide-y"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            {section.terms.map((t) => (
              <div
                key={t.term}
                className="p-4 grid gap-1 md:gap-4 md:grid-cols-[14rem_1fr]"
                style={{ borderColor: 'var(--border)' }}
              >
                <dt
                  className="font-semibold text-sm"
                  style={{ color: 'var(--text-primary)' }}
                >
                  {highlight(t.term, q)}
                </dt>
                <dd
                  className="text-sm leading-relaxed"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  {highlight(t.def, q)}
                </dd>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
