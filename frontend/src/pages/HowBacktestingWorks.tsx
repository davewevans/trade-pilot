import { useRef } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h1>
      <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
        {subtitle}
      </p>
    </div>
  )
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-lg font-semibold mt-10 mb-3"
      style={{ color: 'var(--text-primary)' }}
    >
      {children}
    </h2>
  )
}

function Card({
  children,
  accent,
  topAccent,
}: {
  children: React.ReactNode
  accent?: string
  topAccent?: string
}) {
  return (
    <div
      className="p-5 rounded-lg border"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderLeft: accent ? `4px solid ${accent}` : undefined,
        borderTop: topAccent ? `3px solid ${topAccent}` : undefined,
      }}
    >
      {children}
    </div>
  )
}

function Callout({
  children,
  accent = 'var(--accent)',
}: {
  children: React.ReactNode
  accent?: string
}) {
  return (
    <div
      className="mt-4 p-4 rounded text-sm leading-relaxed"
      style={{
        backgroundColor: `color-mix(in srgb, ${accent} 8%, transparent)`,
        color: 'var(--text-secondary)',
        borderLeft: `3px solid ${accent}`,
      }}
    >
      {children}
    </div>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text-secondary)' }}>
      {children}
    </p>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
      {children}
    </h3>
  )
}

function StepRow({
  n,
  title,
  children,
}: {
  n: number
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="flex gap-4 py-4" style={{ borderTop: '1px solid var(--border)' }}>
      <div
        className="w-7 h-7 shrink-0 rounded-full flex items-center justify-center text-xs font-mono font-semibold mt-0.5"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
          color: 'var(--accent)',
          border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
        }}
      >
        {n}
      </div>
      <div>
        <div className="font-semibold text-sm mb-1" style={{ color: 'var(--text-primary)' }}>
          {title}
        </div>
        <div className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {children}
        </div>
      </div>
    </div>
  )
}

function ParamCard({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div
        className="text-[10px] uppercase tracking-wider font-semibold mb-1"
        style={{ color: 'var(--accent)' }}
      >
        {label}
      </div>
      <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </p>
    </div>
  )
}

type BenchmarkLevel = 'solid' | 'marginal' | 'red'

const BENCHMARK_COLOR: Record<BenchmarkLevel, string> = {
  solid: 'var(--green)',
  marginal: 'var(--yellow, #eab308)',
  red: 'var(--red)',
}

const BENCHMARKS: { metric: string; solid: string; marginal: string; red: string }[] = [
  {
    metric: 'Win Rate',
    solid: '> 70%',
    marginal: '55–70%',
    red: '< 55%',
  },
  {
    metric: 'Profit Factor',
    solid: '> 1.5',
    marginal: '1.0–1.5',
    red: '< 1.0',
  },
  {
    metric: 'Max Drawdown',
    solid: '< 15% of gains',
    marginal: '15–30%',
    red: '> 30%',
  },
  {
    metric: 'Avg Duration',
    solid: '10–20 days',
    marginal: '5–10 or 20–30 days',
    red: '> 30 days',
  },
  {
    metric: 'Total Trades',
    solid: '> 50',
    marginal: '20–50',
    red: '< 20',
  },
]

function BenchmarkCell({ value, level }: { value: string; level: BenchmarkLevel }) {
  return (
    <td
      className="px-4 py-3 text-sm font-mono"
      style={{ color: BENCHMARK_COLOR[level] }}
    >
      {value}
    </td>
  )
}

export function HowBacktestingWorks() {
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <div ref={contentRef} className="max-w-5xl">
      <PageHeader
        title="How Backtesting Works"
        subtitle="Test strategies against years of real market data before risking a dollar."
      />

      {/* ── Section 1: Framing ───────────────────────────────────────── */}
      <Card>
        <P>
          Backtesting answers the question every trader asks: "Would this strategy have worked in
          the past?" Instead of running a paper trading account for months to see if a strategy
          change helps, the backtester simulates years of trades in seconds using historical options
          data from ORATS. It's like a time machine for your trading strategy.
        </P>
        <P>
          The backtester uses the same rules the live bot uses — same delta targets, same DTE
          ranges, same IV rank thresholds, same profit close and stop loss rules, same market regime
          classification. If you change a parameter on the Backtester page and run a test, you're
          seeing exactly what the bot would have done with those settings over the selected time
          period.
        </P>
      </Card>

      {/* ── Section 2: Step by Step ──────────────────────────────────── */}
      <SectionHeader>What the Backtester Does — Step by Step</SectionHeader>
      <P>
        For each trading day in the backtest range, the engine runs this loop:
      </P>

      <div
        className="rounded-lg border px-5"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <StepRow n={1} title="Check the market regime">
          It fetches historical VIX and SPY price data from yfinance and classifies the day as
          BULL, NEUTRAL, BEAR, CRASH, or EUPHORIA — using the same{' '}
          <code className="font-mono text-xs">derive_market_regime()</code> logic the live bot
          uses. It also determines the IV environment (LOW, MODERATE, HIGH) from ORATS historical
          IV rank data for each symbol.
        </StepRow>
        <StepRow n={2} title="Check if the strategy is allowed">
          Just like the live bot's Strategy Router, the engine checks whether the selected strategy
          is eligible under that day's regime and IV environment. Bull put spreads in a CRASH
          regime? Skipped. Iron condors in LOW IV? Skipped. The engine enforces the same routing
          rules — so backtest results reflect the actual conditions under which the bot would have
          traded.
        </StepRow>
        <StepRow n={3} title="Look for an entry candidate">
          For each symbol in the backtest, the engine fetches that day's historical option chain
          from ORATS — every available strike, expiration, delta, bid/ask, and open interest. It
          applies the same filters: delta range, DTE range, IV rank threshold, minimum credit. If a
          qualifying contract exists and no position is already open on that symbol, it enters the
          trade at the historical mid-price.
        </StepRow>
        <StepRow n={4} title="Manage open positions daily">
          Every simulated day, the engine checks each open position against the management rules:
          <ul className="mt-2 space-y-1">
            <li className="flex gap-2">
              <span style={{ color: 'var(--accent)' }}>•</span>
              <span>
                Spread value dropped to 50% of the entry credit?{' '}
                <strong style={{ color: 'var(--green)' }}>Close for profit.</strong>
              </span>
            </li>
            <li className="flex gap-2">
              <span style={{ color: 'var(--accent)' }}>•</span>
              <span>
                Spread value risen to 200% of the entry credit?{' '}
                <strong style={{ color: 'var(--red)' }}>Close for max loss (stop loss).</strong>
              </span>
            </li>
            <li className="flex gap-2">
              <span style={{ color: 'var(--accent)' }}>•</span>
              <span>
                DTE ≤ 7?{' '}
                <strong>Close to avoid gamma risk.</strong>
              </span>
            </li>
            <li className="flex gap-2">
              <span style={{ color: 'var(--accent)' }}>•</span>
              <span>
                Backtest window ends with position still open?{' '}
                <strong>Force-close at the last available price.</strong>
              </span>
            </li>
          </ul>
        </StepRow>
        <StepRow n={5} title="Record the result">
          Each trade is logged with: entry date, exit date, entry credit, exit debit, P&L, exit
          reason, delta at entry, IV rank at entry, market regime at entry, and holding duration.
          If an earnings event fell inside the holding period, the engine also records the implied
          vs. realized move — so you can see whether earnings volatility was accurately priced.
        </StepRow>
      </div>

      <Callout>
        <strong>The engine doesn't use Claude for backtest decisions</strong> — it applies the
        rules mechanically. This is intentional. Backtesting measures the mathematical edge of the
        rules themselves, separate from Claude's judgment. In live trading, Claude adds qualitative
        reasoning on top of these rules — the backtest tells you whether the foundation is solid.
      </Callout>

      {/* ── Section 3: How to Use the Backtester Page ───────────────── */}
      <SectionHeader>How to Use the Backtester Page</SectionHeader>
      <P>
        Navigate to <strong>Research → Backtester</strong> in the sidebar. You'll see a parameters
        panel where you can configure:
      </P>

      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        <ParamCard label="Strategy">
          Which strategy to simulate: Wheel/CSP, Bull Put Spread, Bear Call Spread, Iron Condor, or
          Long Call Vertical.
        </ParamCard>
        <ParamCard label="Symbols">
          Which stocks to test. Pick individual symbols or use preset groups: Index ETFs, Tech,
          Financials, or your full watchlist.
        </ParamCard>
        <ParamCard label="Date Range">
          Start and end dates for the simulation. ORATS historical data goes back to 2007, but 2–3
          years is usually enough to capture different market regimes.
        </ParamCard>
        <ParamCard label="Delta">
          The target short strike delta (e.g., 0.30 = the bot sells options with ~30% probability
          of assignment).
        </ParamCard>
        <ParamCard label="DTE Range">
          Minimum and maximum days to expiration for entry. The live bot defaults to 21–45 days
          for most strategies.
        </ParamCard>
        <ParamCard label="IVR Threshold">
          Minimum IV rank required to enter. 30 means the bot only sells when IV is in the top 70%
          of its yearly range. Higher = fewer trades, potentially better edge per trade.
        </ParamCard>
        <ParamCard label="Profit Close %">
          Percentage of max profit at which the bot closes. 0.50 = close when you've captured 50%
          of the credit. Lower = close earlier, free capital faster, fewer reversals.
        </ParamCard>
        <ParamCard label="Contracts">
          Number of contracts per trade. Usually 1 — the same as the live bot.
        </ParamCard>
        <ParamCard label="Spread Width">
          Distance between strikes in a spread (e.g., 5 = $5 wide). Wider spreads collect more
          premium but carry higher max loss.
        </ParamCard>
      </div>

      <div className="mt-6">
        <Card accent="var(--accent)">
          <CardTitle>What happens when you click "Run Backtest"</CardTitle>
          <P>
            The backtest runs as a background job on the server. You'll see a progress indicator
            showing which date the engine is processing. The first run for a symbol fetches
            historical data from ORATS and caches it locally in SQLite — this can take 30–120
            seconds depending on the date range and number of symbols. Subsequent runs with the
            same symbols and date range are nearly instant because the data is already cached.
          </P>
          <P>
            The results appear automatically when the job finishes. If the server restarts
            mid-run, the job is lost — just click Run again.
          </P>
        </Card>
      </div>

      {/* ── Section 4: Reading the Results ──────────────────────────── */}
      <SectionHeader>Reading the Results</SectionHeader>

      <div className="space-y-4">
        <Card topAccent="var(--accent)">
          <CardTitle>Summary Stats — the six numbers that matter</CardTitle>
          <div className="grid gap-3 md:grid-cols-3 mt-2">
            {[
              {
                label: 'Total P&L',
                body: 'Net profit or loss across all simulated trades. Green = profitable, red = not. Multiply by your typical contract count to estimate real-dollar scale.',
              },
              {
                label: 'Win Rate',
                body: 'Percentage of trades that were profitable. Premium-selling strategies typically aim for 65–80%. Anything below 55% is a warning sign.',
              },
              {
                label: 'Avg Trade',
                body: "Average P&L per trade. Tells you what a 'typical' trade looks like. Compare this to the max loss on a losing trade — if the average winner is $80 but the average loser is $400, the math doesn't work regardless of win rate.",
              },
              {
                label: 'Max Drawdown',
                body: 'The largest peak-to-trough loss during the backtest. This is the worst losing streak — how much pain you would have had to sit through to reach the final result.',
              },
              {
                label: 'Avg Duration',
                body: 'How many days positions are held on average. Should roughly match your DTE target minus however early the 50% profit close triggers. If it\'s too long, your profit target may be set too high.',
              },
              {
                label: 'Total Trades',
                body: 'How many trades the engine found and executed. Fewer than 20 means the results aren\'t statistically meaningful — you need more data before trusting the numbers.',
              },
            ].map((item) => (
              <div
                key={item.label}
                className="p-3 rounded"
                style={{ backgroundColor: 'var(--bg-secondary)' }}
              >
                <div
                  className="text-xs font-semibold mb-1"
                  style={{ color: 'var(--text-primary)' }}
                >
                  {item.label}
                </div>
                <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                  {item.body}
                </p>
              </div>
            ))}
          </div>
        </Card>

        <Card topAccent="var(--accent)">
          <CardTitle>Equity Curve</CardTitle>
          <P>
            Shows cumulative P&L over time. An upward-trending line with small dips = healthy
            strategy with consistent edge. A line that surges then crashes = the strategy works
            until a specific regime (usually a sharp bear market) wipes out months of gains. A flat
            or downward line = no edge with these parameters.
          </P>
          <P>
            Look at what's happening in the market during the dips — are losses clustering around
            2020 (COVID), 2022 (rate hikes), or random one-off months? Clustered losses suggest a
            regime sensitivity you can address by adjusting parameters or adding a regime filter.
          </P>
        </Card>

        <Card topAccent="var(--accent)">
          <CardTitle>Monthly Returns</CardTitle>
          <P>
            P&L by month. Look for consistency — a strategy that makes money 8 out of 12 months is
            more reliable than one that makes a fortune in January and loses it all in March. If the
            strategy is consistently losing in a specific month or season, that pattern is worth
            investigating.
          </P>
        </Card>

        <Card topAccent="var(--accent)">
          <CardTitle>Trade Table</CardTitle>
          <P>
            Every simulated trade with entry/exit dates, strikes, credit, P&L, exit reason, and
            the conditions at entry (IVR, delta, regime). You can filter to show only winners or
            losers.
          </P>
          <P>
            <strong>Study the losers specifically.</strong> Do they cluster around earnings dates?
            During CRASH regimes? When IVR was borderline (just above your threshold)? That tells
            you exactly where the strategy breaks and which parameter to adjust.
          </P>
        </Card>
      </div>

      {/* ── Section 5: Good Results ──────────────────────────────────── */}
      <SectionHeader>What Good Results Look Like</SectionHeader>
      <P>
        Use these benchmarks to evaluate a backtest. Numbers alone don't tell the full story — a
        strategy that barely passes on all five metrics is more trustworthy than one that crushes
        win rate but has a terrible profit factor.
      </P>

      <div
        className="rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr
              className="text-left text-xs uppercase tracking-wider"
              style={{ color: 'var(--text-muted)' }}
            >
              <th className="px-4 py-3 font-medium">Metric</th>
              <th className="px-4 py-3 font-medium" style={{ color: 'var(--green)' }}>
                Solid
              </th>
              <th className="px-4 py-3 font-medium" style={{ color: 'var(--yellow, #eab308)' }}>
                Marginal
              </th>
              <th className="px-4 py-3 font-medium" style={{ color: 'var(--red)' }}>
                Red Flag
              </th>
            </tr>
          </thead>
          <tbody>
            {BENCHMARKS.map((row) => (
              <tr
                key={row.metric}
                style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              >
                <td className="px-4 py-3 font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                  {row.metric}
                </td>
                <BenchmarkCell value={row.solid} level="solid" />
                <BenchmarkCell value={row.marginal} level="marginal" />
                <BenchmarkCell value={row.red} level="red" />
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
        * Profit Factor = total $ won ÷ total $ lost. Above 1.0 means winners outpace losers in
        dollar terms.
      </p>

      <Callout accent="var(--red)">
        <strong>A 75% win rate with a 0.8 profit factor is a losing strategy.</strong> It means
        you win often but your losers are so much bigger than your winners that the math doesn't
        work over time. The bot's 50% profit close and 200% stop loss are designed specifically to
        keep profit factor above 1.0 — you give up the last 50% of potential profit on each winner
        to prevent losses from compounding unchecked.
      </Callout>

      {/* ── Section 6: Improve the Bot ──────────────────────────────── */}
      <SectionHeader>Using Backtests to Improve the Bot</SectionHeader>
      <P>
        The backtester's real value isn't confirming that a strategy is profitable — it's helping
        you fine-tune parameters. Here's how to use it effectively:
      </P>

      <div className="grid gap-3 md:grid-cols-2">
        <Card accent="var(--accent)">
          <CardTitle>Compare delta targets</CardTitle>
          <P>
            Run the same strategy on SPY with delta 0.20, 0.25, and 0.30. Which has the best
            profit factor? A higher delta collects more premium but gets breached more often. The
            optimal delta depends on the specific symbol and market regime mix in your date range.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Test IVR thresholds</CardTitle>
          <P>
            Run with IVR threshold 25, 30, 35, and 40. Higher thresholds mean fewer trades but
            potentially better edge per trade. Find the sweet spot where win rate stays high but
            you're not sitting out too many opportunities.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Compare profit close levels</CardTitle>
          <P>
            Does closing at 40% vs 50% vs 60% change overall P&L? Closing earlier reduces
            per-trade profit but frees capital faster and cuts exposure to late-trade reversals. The
            right level depends on how often positions reverse before expiration.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Stress-test across regimes</CardTitle>
          <P>
            Run across 2020 (COVID crash), 2022 (bear market), and 2023–2024 (bull recovery). A
            strategy that holds up across all three periods is far more trustworthy than one that
            only works in low-volatility bull markets.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Check symbol suitability</CardTitle>
          <P>
            Run the same strategy on every symbol in your watchlist separately. Some stocks are
            excellent for bull put spreads (low drawdown, consistent wins) and terrible for iron
            condors (too much movement). Let the data tell you which symbols belong in which
            watchlist.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Isolate earnings impact</CardTitle>
          <P>
            Filter the trade table to trades where <em>earnings occurred in the holding window</em>.
            If that subset has dramatically worse results, tighten your earnings block distance. If
            it doesn't, the current block may already be filtering correctly.
          </P>
        </Card>
      </div>

      {/* ── Section 7: Live Decision Connection (Future) ─────────────── */}
      <SectionHeader>How Backtests Feed Into Live Decisions</SectionHeader>
      <P>
        The backtester is currently a research tool — you run it manually to evaluate strategy
        changes. Planned enhancements will connect it directly to the bot's live decision-making:
      </P>

      <div className="grid gap-4 md:grid-cols-2">
        <Card topAccent="var(--text-muted)">
          <div
            className="text-[10px] uppercase tracking-wider font-semibold mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            Planned
          </div>
          <CardTitle>Weekly Pre-Computed Stats</CardTitle>
          <P>
            Every Sunday evening, the bot would automatically run backtests across the full
            watchlist and cache the results. When Claude evaluates a trade on Monday morning, its
            context would include: "Bull put spreads on SPY with these parameters have a 73% win
            rate over 847 historical trades." This gives Claude a statistical baseline to weigh
            against current market conditions — not just rules, but evidence.
          </P>
        </Card>
        <Card topAccent="var(--text-muted)">
          <div
            className="text-[10px] uppercase tracking-wider font-semibold mb-2"
            style={{ color: 'var(--text-muted)' }}
          >
            Planned
          </div>
          <CardTitle>Similar Trade Matching</CardTitle>
          <P>
            When the bot finds a specific candidate — say SPY bull put spread, IVR 48, delta -0.25,
            28 DTE — it would search the historical trade database for the 5 most similar past
            trades and include their outcomes in Claude's context. "Three of the five similar trades
            were profitable, and both losers occurred during VIX spikes above 28." Claude could
            then factor in whether today's conditions look more like the winners or the losers.
          </P>
        </Card>
      </div>

      {/* ── Section 8: Limitations ──────────────────────────────────── */}
      <SectionHeader>Limitations — What Backtesting Can't Tell You</SectionHeader>
      <P>
        Backtesting is a powerful research tool, but it has real limits. Understanding them keeps
        you from over-trusting the results.
      </P>

      <div className="grid gap-3 md:grid-cols-2">
        <Card>
          <CardTitle>No slippage modeling</CardTitle>
          <P>
            The backtester enters and exits at the historical mid-price. In live trading, you won't
            always get the mid — especially on multi-leg spreads where each leg has its own
            bid/ask spread. Real P&L will typically be slightly worse than backtest P&L, particularly
            for iron condors with four legs.
          </P>
        </Card>
        <Card>
          <CardTitle>Survivorship bias</CardTitle>
          <P>
            The symbols in your watchlist exist today. Companies that went bankrupt, were acquired,
            or were delisted aren't in ORATS historical data. This slightly inflates historical win
            rates — the stocks you're testing against all survived.
          </P>
        </Card>
        <Card>
          <CardTitle>Past performance ≠ future results</CardTitle>
          <P>
            A strategy that worked from 2020–2025 might not work in 2026. Market structure changes,
            correlations shift, and volatility regimes evolve. The backtester tells you what HAS
            worked — not what WILL work. Use it to build confidence in a framework, not to guarantee
            an outcome.
          </P>
        </Card>
        <Card>
          <CardTitle>No Claude</CardTitle>
          <P>
            The backtester applies rules mechanically. The live bot has Claude making judgment
            calls — skipping trades that look bad despite passing all filters, or flagging unusual
            conditions that don't fit a neat category. Claude's qualitative reasoning isn't captured
            in backtest results. Real performance will differ.
          </P>
        </Card>
        <Card>
          <CardTitle>End-of-day data only</CardTitle>
          <P>
            The backtester uses ORATS end-of-day data, not intraday prices. A position that briefly
            hit the stop loss at 11:00 AM but recovered by close would show as a hold in the
            backtest — but the live bot's intraday checks would have closed it. This means backtest
            results may slightly undercount stop-loss exits.
          </P>
        </Card>
        <Card>
          <CardTitle>Earnings estimation</CardTitle>
          <P>
            When the exact long leg price isn't available in historical data, the engine estimates
            it proportionally from the short leg price. These estimates are accurate on average but
            can vary on individual trades, slightly affecting P&L precision on spread positions.
          </P>
        </Card>
      </div>

      {/* ── Footer ───────────────────────────────────────────────────── */}
      <div className="mt-10">
        <P>
          For the interactive backtester, go to{' '}
          <strong>Research → Backtester</strong>. For how the bot makes live decisions, see{' '}
          <strong>How Claude Decides</strong>. For the specific strategy parameters, see{' '}
          <strong>Strategies</strong>.
        </P>
        <p className="text-xs text-center mt-6 mb-4" style={{ color: 'var(--text-muted)' }}>
          Backtest results use ORATS historical options data cached locally in SQLite. First runs
          fetch from the ORATS API; subsequent runs with the same symbols and date range use cached
          data.
        </p>
      </div>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
