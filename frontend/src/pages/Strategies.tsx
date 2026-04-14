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

function AccountSection({
  accent,
  header,
  tagline,
  children,
}: {
  accent: string
  header: string
  tagline: string
  children: React.ReactNode
}) {
  return (
    <section
      className="mb-10 rounded-lg border overflow-hidden"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div
        className="px-5 py-4 border-b"
        style={{
          borderColor: 'var(--border)',
          background: `linear-gradient(90deg, color-mix(in srgb, ${accent} 14%, transparent), transparent)`,
          borderLeft: `4px solid ${accent}`,
        }}
      >
        <h2 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
          {header}
        </h2>
        <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
          {tagline}
        </p>
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}

function PhaseStepper({ steps }: { steps: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      {steps.map((step, i) => (
        <div key={i} className="flex items-center gap-2">
          <span
            className="text-xs px-3 py-1.5 rounded-full font-mono"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--accent) 14%, transparent)',
              color: 'var(--text-primary)',
              border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
            }}
          >
            {step}
          </span>
          {i < steps.length - 1 && (
            <span style={{ color: 'var(--text-muted)' }}>→</span>
          )}
        </div>
      ))}
    </div>
  )
}

function SubCard({
  title,
  children,
  bullets,
}: {
  title: string
  children?: React.ReactNode
  bullets?: string[]
}) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <h4 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h4>
      {children && (
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {children}
        </p>
      )}
      {bullets && (
        <ul className="mt-2 space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
          {bullets.map((b, i) => (
            <li key={i} className="flex gap-2">
              <span style={{ color: 'var(--accent)' }}>•</span>
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Prose({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-sm leading-relaxed mb-4"
      style={{ color: 'var(--text-secondary)' }}
    >
      {children}
    </p>
  )
}

function Subheading({ children }: { children: React.ReactNode }) {
  return (
    <h3
      className="text-xs uppercase tracking-wider font-semibold mt-5 mb-2"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h3>
  )
}

function CondorDiagram() {
  return (
    <div
      className="my-3 p-4 rounded-lg font-mono text-xs leading-relaxed"
      style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
    >
      <div className="flex justify-between items-end mb-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        <span>Stock falls too far</span>
        <span style={{ color: 'var(--green)' }}>Profit zone</span>
        <span>Stock rises too far</span>
      </div>
      <div className="flex items-end h-16">
        <div className="flex-1 border-r" style={{ borderColor: 'var(--red)' }}>
          <div className="h-4" style={{ backgroundColor: 'color-mix(in srgb, var(--red) 25%, transparent)' }} />
        </div>
        <div className="flex-[2]" style={{ backgroundColor: 'color-mix(in srgb, var(--green) 25%, transparent)', height: '100%' }} />
        <div className="flex-1 border-l" style={{ borderColor: 'var(--red)' }}>
          <div className="h-4" style={{ backgroundColor: 'color-mix(in srgb, var(--red) 25%, transparent)' }} />
        </div>
      </div>
      <div className="flex justify-between mt-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        <span>← Put spread</span>
        <span>Short put / Short call →</span>
        <span>Call spread →</span>
      </div>
    </div>
  )
}

const ROUTING_ROWS = [
  { regime: 'NEUTRAL', iv: 'HIGH (IVR ≥ 50)', strategy: 'Iron Condor' },
  { regime: 'NEUTRAL or BULL', iv: 'MODERATE or HIGH', strategy: 'Bull Put Spread' },
  { regime: 'BEAR or NEUTRAL', iv: 'MODERATE or HIGH', strategy: 'Bear Call Spread' },
  { regime: 'BULL', iv: 'LOW (IVR < 30)', strategy: 'Long Call Vertical' },
  { regime: 'CRASH', iv: 'Any', strategy: 'No new positions' },
  { regime: 'EUPHORIA', iv: 'Any', strategy: 'Wheel only (cautious)' },
]

export function Strategies() {
  const contentRef = useRef<HTMLDivElement>(null)
  return (
    <div ref={contentRef} className="max-w-5xl">
      <PageHeader
        title="Strategies"
        subtitle="What each account does, in plain language."
      />

      {/* --- Wheel --- */}
      <AccountSection
        accent="var(--accent-wheel, var(--accent))"
        header="Wheel Strategy — Income Through Stock Ownership"
        tagline="A three-phase income strategy that cycles automatically."
      >
        <Prose>
          The Wheel is a three-phase income strategy. The bot cycles through these phases automatically:
        </Prose>
        <PhaseStepper
          steps={[
            'IDLE',
            'Sell Cash-Secured Put',
            '(if assigned) Own Stock',
            'Sell Covered Call',
            'repeat',
          ]}
        />

        <Subheading>Phases</Subheading>
        <div className="grid gap-3 md:grid-cols-3">
          <SubCard
            title="1. Sell a Cash-Secured Put (CSP)"
            bullets={[
              'Target delta: -0.20 to -0.30',
              'Time to expiration: 21–35 days',
              'IV Rank must be ≥ 30 (we sell when options are expensive)',
              'Max 3 positions per sector (forces diversification)',
            ]}
          >
            We sell someone the right to sell us 100 shares of a stock at a specific price (the strike).
            We collect premium upfront. If the stock stays above our strike, we keep the premium and start
            over. If it falls below, we buy the shares — which is fine, because we wanted to own them.
          </SubCard>
          <SubCard title="2. Hold the Shares">
            We now own 100 shares. We wait for the right moment to sell a covered call.
          </SubCard>
          <SubCard
            title="3. Sell a Covered Call (CC)"
            bullets={[
              'Strike must be above our cost basis (we never lock in a loss)',
              'Target delta: 0.20 to 0.35',
            ]}
          >
            We sell someone the right to buy our 100 shares at a higher price. We collect more premium.
            If the stock stays below the strike, we keep the shares and the premium. If it rises above,
            our shares get called away at a profit.
          </SubCard>
        </div>

        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Cost basis tracking:</strong> The bot tracks effective cost basis across the full wheel cycle — not just
          the assignment price, but assignment price minus all CSP and roll premiums collected. Covered call strikes
          are always set above this effective cost basis, never just the raw assignment price.
        </div>

        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Sector diversification:</strong> The bot limits concurrent wheel positions to a maximum of 3 per sector.
          The watchlist spans multiple sectors (technology, financials, energy, index) to avoid correlated losses
          when a single sector sells off.
        </div>

        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Wheel watchlist:</strong> Each cycle the bot scans the entire wheel watchlist —
          not just a single symbol. It pre-checks each symbol and evaluates qualifying candidates
          before asking Claude for the best setup. The wheel watchlist is smaller than the other
          two (stocks you'd be comfortable owning through assignment). It lives in{' '}
          <code className="font-mono">data/watchlist.json</code> and can be edited from the
          Watchlist page in the dashboard without redeploying.
        </div>

        <Subheading>How the bot manages positions</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>50% profit close:</strong> If we've captured 50% of the premium, the bot closes early — this captures ~70% of the theta income with far less gamma risk.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Roll on delta doubling:</strong> If delta doubles from entry (e.g., -0.25 → -0.50), the bot rolls to a better strike — but ONLY for a net credit of at least $0.10. No rolling for a debit.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>DTE ≤ 7 exit:</strong> If expiration is within 7 days and the position is at risk, the bot rolls out. If profitable, it closes.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Max 2 rolls per position:</strong> After two rolls, if still at risk, the position is closed rather than continuing to roll indefinitely.</li>
        </ul>

        <Subheading>When the bot sells assigned shares at a loss</Subheading>
        <Prose>
          Assignment is not a failure — it's the wheel working as designed. But if the stock deteriorates significantly
          after assignment, holding and writing covered calls on a falling position locks up capital unproductively.
          The bot has concrete exit triggers:
        </Prose>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--red, #ef4444)' }}>•</span> Stock drops &gt; 25% below cost basis AND falls below the 200-day SMA → sell shares</li>
          <li className="flex gap-2"><span style={{ color: 'var(--red, #ef4444)' }}>•</span> Two or more analyst downgrades in the past 14 days → sell shares</li>
          <li className="flex gap-2"><span style={{ color: 'var(--yellow, #eab308)' }}>•</span> Stock drops &gt; 15% below cost basis with a single downgrade → flagged for review</li>
          <li className="flex gap-2"><span style={{ color: 'var(--yellow, #eab308)' }}>•</span> VIX in CRASH regime (&gt; 35) → hold (don't write CCs or sell into panic — wait for clarity)</li>
        </ul>
      </AccountSection>

      {/* --- Iron Condor --- */}
      <AccountSection
        accent="var(--accent-iron-condor, var(--accent))"
        header="Iron Condor — Profiting from a Quiet Market"
        tagline="Defined-risk strategy that profits when a stock stays in a range."
      >
        <Prose>
          An iron condor profits when a stock stays within a defined price range. We simultaneously sell an
          OTM put spread (below the stock) and an OTM call spread (above the stock). As long as the stock
          doesn't move too far in either direction, we keep the premium from both spreads.
        </Prose>

        <CondorDiagram />

        <Subheading>When the bot uses this strategy</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> High IV environment only (IV Rank ≥ 50 — options are expensive, we sell the premium)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Neutral market regime only — never in trending markets</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> VIX between 18 and 35 (enough premium to justify 4 legs, not extreme panic)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Earnings must be &gt; 30 days away (extra buffer for a 4-leg position)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Both short strikes placed outside the 1× implied move (using ORATS data)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Combined credit ≥ $1.25 (covers transaction costs on 8 legs: 4 to open, 4 to close)</li>
        </ul>

        <Subheading>Risk profile</Subheading>
        <div className="grid gap-3 md:grid-cols-3">
          <SubCard title="Max profit">Premium collected from both spreads.</SubCard>
          <SubCard title="Max loss">
            Width of one spread minus premium collected (defined and limited).
          </SubCard>
          <SubCard title="Break-evens">
            Short put strike minus premium / short call strike plus premium.
          </SubCard>
        </div>

        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Iron condor watchlist:</strong> The bot scans its own dedicated iron condor
          watchlist each cycle — favoring liquid, range-bound names where selling both sides
          makes sense. This is separate from the wheel and spreads watchlists, and is also
          managed from the Watchlist page in the dashboard.
        </div>

        <Subheading>How the bot manages this position</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Treated as a single unit</strong> — the bot never rolls or adjusts one side independently. If either side is threatened, the entire condor is closed.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>50% profit close:</strong> Close when combined spread value drops to ≤ 50% of original credit.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>200% stop loss:</strong> Close when combined value reaches ≥ 200% of original credit.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Delta doubling:</strong> If either short leg's delta doubles from entry, close the condor (one side is being tested).</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>DTE ≤ 7:</strong> Close regardless — gamma risk on both wings is too high.</li>
        </ul>
      </AccountSection>

      {/* --- Spreads --- */}
      <AccountSection
        accent="var(--accent-spreads, var(--accent))"
        header="Adaptive Spreads — Three Strategies, One Account"
        tagline="The bot picks the right strategy each day based on market conditions."
      >
        <Prose>
          This account runs three different strategies. The bot picks which one to use each day based on
          current market conditions. Only one new position can be opened per cycle — the bot won't stack
          multiple new entries.
        </Prose>

        <Subheading>Routing logic</Subheading>
        <div
          className="rounded-lg border overflow-x-auto"
          style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr style={{ color: 'var(--text-muted)' }} className="text-left text-xs uppercase tracking-wider">
                <th className="px-4 py-3 font-medium">Market Regime</th>
                <th className="px-4 py-3 font-medium">IV Environment</th>
                <th className="px-4 py-3 font-medium">Strategy Used</th>
              </tr>
            </thead>
            <tbody>
              {ROUTING_ROWS.map((r) => (
                <tr key={r.regime + r.iv} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                  <td className="px-4 py-3 font-mono text-xs">{r.regime}</td>
                  <td className="px-4 py-3 font-mono text-xs">{r.iv}</td>
                  <td className="px-4 py-3" style={{ color: 'var(--text-primary)' }}>{r.strategy}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          If the circuit breaker is YELLOW or RED, no new positions are opened in this account regardless of conditions.
        </div>

        <Subheading>The three strategies</Subheading>
        <div className="grid gap-3 md:grid-cols-3">
          <SubCard
            title="Bull Put Spread (Bullish / Neutral)"
            bullets={[
              'Max loss capped at spread width minus credit received',
              'Used in MODERATE or HIGH IV, NEUTRAL or BULL regime',
              'DTE: 21–35 days',
              'Minimum credit: $0.75 (covers transaction costs)',
              'Credit-to-width ratio ≥ 15%',
            ]}
          >
            We sell a put and buy a cheaper put below it. We profit if the stock stays above our short
            strike. The long put below limits our maximum loss. A defined-risk way to collect premium when
            we're neutral to slightly bullish. Management: 50% profit close, 200% stop loss, close at DTE ≤ 7.
          </SubCard>
          <SubCard
            title="Bear Call Spread (Bearish / Neutral)"
            bullets={[
              'Mirror image of the bull put spread, on the call side',
              'Used in MODERATE or HIGH IV, BEAR or NEUTRAL regime',
              'DTE: 21–35 days',
              'Minimum credit: $0.75',
              'Ex-dividend and earnings dates checked before entry',
            ]}
          >
            We sell a call and buy a cheaper call above it. We profit if the stock stays below our short
            strike. Used when we expect the market to be flat to slightly bearish. Requires bearish technical
            confirmation — stock at resistance, below 50-day SMA, or RSI overbought.
          </SubCard>
          <SubCard
            title="Long Call Vertical (Bullish, Low IV)"
            bullets={[
              'Debit strategy — we pay premium upfront',
              'Profit and risk both capped',
              'Only in BULL regime + LOW IV + confirmed support bounce (CAHOLD)',
              'DTE: 30–45 days',
              'Max debit: $2.00 per spread',
            ]}
          >
            Unlike the other two, this is a debit strategy — we pay to open it. We buy a call and sell a
            higher-strike call above it. We profit if the stock rises above our long strike plus the debit paid.
            Only deployed when options are cheap (low IV) and the market is clearly bullish with a technical
            support bounce signal. Tighter management: 100% profit target (spread doubles), 40% stop loss,
            close if 60% of time has elapsed without meaningful gain.
          </SubCard>
        </div>

        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Multi-underlying scanning:</strong> Each spread strategy scans its own dedicated watchlist (the largest of the three — any liquid options chain qualifies). The bot pre-checks every symbol, scores the candidates by EV and regime fit, and only calls Claude once for the single strongest setup. At most one new spread opens per cycle. The watchlist lives in <code className="font-mono">data/watchlist.json</code> and is editable from the Watchlist page without redeploying.
        </div>
      </AccountSection>

      {/* --- Portfolio Risk --- */}
      <div className="mt-8">
        <Subheading>Portfolio-Level Risk Controls</Subheading>
        <Prose>
          Individual strategy parameters don't tell the full story. The bot also enforces portfolio-wide limits:
        </Prose>
        <div className="grid gap-3 md:grid-cols-2 mt-3">
          <SubCard title="Position Sizing Caps">
            <ul className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Wheel: max 10% of buying power per CSP, max 5 concurrent positions</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Iron Condor: max loss ≤ 5% of buying power</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Credit spreads: max loss ≤ 2% of buying power per spread</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Debit spreads: max risk ≤ 1% of buying power</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Shared account (3 spread strategies): combined max loss ≤ 10% of account</li>
            </ul>
          </SubCard>
          <SubCard title="Sector & Correlation Awareness">
            <ul className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Max 3 wheel positions per sector (prevents concentrated sector bets)</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Watchlist spans Technology, Financials, Energy, and Index ETFs</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Spread strategies scan across the full watchlist for uncorrelated setups</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Circuit breaker monitors aggregate equity across all three accounts</li>
            </ul>
          </SubCard>
        </div>

        <Subheading>Entry Timing</Subheading>
        <Prose>
          The bot evaluates new entries at 10:00 AM ET — not at the 9:30 open. Options bid-ask spreads are 2-3× wider
          in the first 30 minutes, and quoted Greeks (especially delta) are unreliable. Waiting 30 minutes for the options
          market to settle means better fill prices and more accurate contract selection.
        </Prose>

        <Subheading>Worst-case scenario math</Subheading>
        <div
          className="mt-2 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--red, #ef4444) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--red, #ef4444)',
          }}
        >
          If every position hits max loss simultaneously: 5 wheel assignments (~50% of buying power in stock) plus
          all spreads at max loss (~10%) = ~60% of capital at risk. The diversified watchlist and sector limits reduce
          the probability of this happening, but it's not zero. The circuit breaker's 15% drawdown lock exists specifically
          to halt before this scenario fully materializes.
        </div>
      </div>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
