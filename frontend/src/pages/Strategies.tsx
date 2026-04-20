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

function InactiveBadge({ color }: { color: string }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: '4px',
      fontSize: '11px',
      fontWeight: 600,
      backgroundColor: `color-mix(in srgb, ${color} 15%, transparent)`,
      color: color,
      border: `1px solid ${color}`,
      marginBottom: '12px',
    }}>
      INACTIVE — Account configured, not yet activated for trading
    </span>
  )
}

const ROUTING_ROWS = [
  { regime: 'NEUTRAL', iv: 'HIGH (IVR ≥ 50)', strategy: 'Iron Condor (Paper Account 3)', note: 'IV must be overvalued or fair per ORATS forecast' },
  { regime: 'NEUTRAL', iv: 'HIGH (IVR ≥ 50)', strategy: 'Iron Butterfly (Paper Account 4)', note: 'Higher premium, narrower profit zone — dedicated account' },
  { regime: 'NEUTRAL or BULL', iv: 'LOW or MODERATE', strategy: 'Calendar Spread (Paper Account 5)', note: 'Requires positive contango' },
  { regime: 'NEUTRAL or BULL', iv: 'MODERATE or HIGH', strategy: 'Bull Put Spread (Adaptive)', note: 'Spread yield ≥ 0.1%' },
  { regime: 'BEAR or NEUTRAL', iv: 'MODERATE or HIGH', strategy: 'Bear Call Spread (Adaptive)', note: 'Spread yield ≥ 0.1%' },
  { regime: 'BULL', iv: 'LOW (IVR < 30)', strategy: 'Long Call Vertical (Adaptive)', note: 'IV must not be overvalued per ORATS' },
  { regime: 'Any (non-CRASH)', iv: 'MODERATE or HIGH', strategy: 'Standard Wheel (CSP → CC)', note: 'Dedicated account' },
  { regime: 'CRASH', iv: 'Any', strategy: 'No new positions', note: '' },
  { regime: 'EUPHORIA', iv: 'Any', strategy: 'Wheel only (cautious)', note: '' },
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
        header="Standard Wheel — Income Through Stock Ownership"
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

      {/* --- Turnover Wheel --- */}
      <AccountSection
        accent="var(--accent-wheel, var(--accent))"
        header="Turnover Wheel — Higher-Turnover Variant"
        tagline="A second wheel account designed for fast share turnover — shorter CC cycles, no delta cap on CC strike, cost-basis-only selection. Same market as the original Wheel; different philosophy."
      >
        <Prose>
          The Turnover Wheel runs the same four-phase cycle as the original Wheel, but follows a different
          philosophy for the covered call phase. The idea: get rid of assigned shares quickly and return to
          selling cash-secured puts, which are more capital-efficient. Paper-traded in its own Alpaca account
          so we can compare results directly against the original Wheel.
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

        <Subheading>What's different from the original Wheel</Subheading>
        <div className="grid gap-3 md:grid-cols-2">
          <SubCard
            title="Shorter covered calls (7–14 DTE)"
            bullets={[
              'Original Wheel: CCs are 21–35 days out',
              'Turnover: CCs are 7–14 days out',
              'More CC cycles per year, faster share turnover',
            ]}
          >
            The goal is to get called away quickly and return to selling puts. Shorter DTE means we write more
            CCs per year and collect premium more often, at the cost of higher gamma risk per contract.
          </SubCard>
          <SubCard
            title="Cost-basis strike selection"
            bullets={[
              'Original Wheel: strike must be above the upper Bollinger Band AND above cost basis',
              'Turnover: strike must be above cost basis, period',
              'No technical-indicator filter on CC strike',
              'No delta cap on the short call — the cost-basis filter is the sole strike constraint',
            ]}
          >
            We remove both the Bollinger Band check and the delta cap that the standard Wheel uses on covered calls.
            As long as the strike is above our net cost basis (so we can't lock in a loss), the bot can sell the CC
            at any delta the market is offering. The tradeoff: we may sell CCs much closer to the money — sometimes
            ITM — which dramatically increases assignment frequency. That's the goal here, not a bug.
          </SubCard>
          <SubCard
            title="Smaller, more diversified positions"
            bullets={[
              'Original Wheel: max 10% of buying power per position, 5 concurrent',
              'Turnover: max 5% of buying power per position, 10 concurrent',
              'Spreads capital across more names',
            ]}
          >
            Halving the per-position size and doubling the concurrent cap pushes the account toward
            diversification over concentration.
          </SubCard>
          <SubCard
            title="More roll flexibility"
            bullets={[
              'Original Wheel: max 2 rolls',
              'Turnover: max 3 rolls, wider delta window',
              'Rolls still require net credit (never debit)',
            ]}
          >
            The original Wheel gives up on a position after two rolls. The Turnover Wheel allows one more
            roll attempt and widens the acceptable delta range on the replacement contract (short put within
            −0.40, short call within 0.45). Net-credit requirement is unchanged — no rolls for a debit.
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
          <strong>Why no delta cap?</strong> The Turnover Wheel's goal is fast share turnover — get called away, return to selling puts. Capping short-call delta would slow that turnover by pushing strikes further OTM. Cost basis is the only hard rule because it's the only rule that prevents locking in a loss; everything else about the CC leg is optimized for speed of cycle rather than safety margin on the strike.
        </div>

        <Subheading>What's the same as the original Wheel</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> CSP entry: target delta −0.20 to −0.30, 21–35 DTE, IV Rank ≥ 30</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Earnings avoidance: no entries within 21 days of an earnings announcement</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Open interest minimum: ≥ 200 (CSP entry)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> 50% profit close on both CSPs and CCs</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> All universal guardrails apply — OCC regex validation, limit orders only, qty = 1, credit-sign enforcement</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Watchlist: starts as a copy of the original Wheel's watchlist; current contents are shown on the Watchlist page</li>
        </ul>

        <div
          className="mt-4 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Why run both Wheels?</strong> Paper trading lets us test two philosophies in parallel without risking real capital. Both wheels run on the same watchlist and same market conditions, so performance differences come from the rule differences listed above rather than stock selection or timing. The Turnover Wheel is the more aggressive of the two on a per-position basis (no CC delta cap, shorter cycles); the standard Wheel is more aggressive on a per-position-size basis (10% BP per position vs. 5%). Give it at least three months before reading too much into the comparison — options strategies don't produce meaningful signal in a few weeks.
        </div>
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
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Earnings must be &gt; 35 days away (extra buffer for a 4-leg position)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Both short strikes placed outside the 1× implied move (using ORATS data)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Combined credit ≥ $1.25 (covers transaction costs on 8 legs: 4 to open, 4 to close)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> ORATS IV forecast must confirm options are overvalued or fairly priced (never enter when IV is undervalued)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Term structure must NOT be in backwardation (contango required for neutral thesis)</li>
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

      {/* --- Iron Butterfly --- */}
      <AccountSection
        accent="var(--accent-butterfly, var(--purple, #A855F7))"
        header="Iron Butterfly — Maximum Premium at the Money"
        tagline="Higher premium than iron condor, tighter profit zone. Not yet active."
      >
        <InactiveBadge color="var(--purple, #A855F7)" />

        <Prose>
          An iron butterfly sells an ATM put and an ATM call at the <strong>same center
          strike</strong>, then buys an OTM put wing below and an OTM call wing above for
          protection. All four legs share the same expiration. It is a defined-risk credit
          strategy — maximum profit is achieved when the underlying closes exactly at the
          center strike at expiration.
        </Prose>

        <Prose>
          Compared to the iron condor, both short strikes are ATM instead of OTM. This
          produces a higher credit but a much narrower profit zone (tent shape vs. plateau
          shape). Use the butterfly when there is high conviction the underlying will stay
          very close to a specific price.
        </Prose>

        <Subheading>Entry criteria</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Market regime: NEUTRAL only</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> IV environment: HIGH (IVR ≥ 50)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> ORATS IV forecast: OVERVALUED or FAIR (never UNDERVALUED)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Term structure: must NOT be in backwardation</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Earnings &gt; 30 days away</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Both short strikes ATM at the same center strike (closest to current price)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Wing width: $5 on each side (put wing and call wing)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Total credit ≥ $2.00 (butterfly collects more than a condor)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Credit-to-width ratio ≥ 30%</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> DTE: 20–45 days</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Both wings: OI ≥ 200, bid-ask &lt; 15%</li>
        </ul>

        <Subheading>Risk profile</Subheading>
        <div className="grid gap-3 md:grid-cols-3">
          <SubCard title="Max profit">
            Premium collected. Achieved when the underlying closes exactly at the center strike.
          </SubCard>
          <SubCard title="Max loss">
            Wing width minus premium collected. Defined and capped at the wings.
          </SubCard>
          <SubCard title="Break-evens">
            Center strike ± total credit. Narrower than the iron condor.
          </SubCard>
        </div>

        <Subheading>Iron Condor vs. Iron Butterfly</Subheading>
        <div
          className="rounded-lg border overflow-x-auto"
          style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr style={{ color: 'var(--text-muted)' }} className="text-left text-xs uppercase tracking-wider">
                <th className="px-4 py-3 font-medium">Feature</th>
                <th className="px-4 py-3 font-medium">Iron Condor</th>
                <th className="px-4 py-3 font-medium">Iron Butterfly</th>
              </tr>
            </thead>
            <tbody>
              {[
                { feature: 'Short strikes', condor: 'OTM (different strikes)', butterfly: 'ATM (same strike)' },
                { feature: 'Profit zone', condor: 'Wider plateau', butterfly: 'Narrow tent' },
                { feature: 'Premium collected', condor: 'Lower', butterfly: 'Higher' },
                { feature: 'Conviction required', condor: 'Range-bound', butterfly: 'Near specific price' },
              ].map((r) => (
                <tr key={r.feature} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                  <td className="px-4 py-3 font-medium" style={{ color: 'var(--text-primary)' }}>{r.feature}</td>
                  <td className="px-4 py-3">{r.condor}</td>
                  <td className="px-4 py-3">{r.butterfly}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <Subheading>How the bot manages this position</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>50% profit close:</strong> Close when spread value drops to ≤ 50% of original credit.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>200% stop loss:</strong> Close when value reaches ≥ 200% of original credit.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>DTE ≤ 7:</strong> Close regardless — gamma risk on all 4 legs.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Wing breach:</strong> If the underlying moves beyond a protective wing, close immediately.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Vol-of-vol tightening:</strong> Tighten profit target to 40% when vol-of-vol is HIGH.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Treated as a single unit</strong> — never close or adjust individual legs.</li>
        </ul>
      </AccountSection>

      {/* --- Calendar Spread --- */}
      <AccountSection
        accent="var(--accent-calendar, var(--teal, #14B8A6))"
        header="Calendar Spread — Profiting From Time Decay Differentials"
        tagline="Neutral, defined-risk time decay play. Not yet active."
      >
        <InactiveBadge color="var(--teal, #14B8A6)" />

        <Prose>
          A calendar spread sells a short-term option and buys a longer-term option at the same strike.
          The short option decays faster than the long option, and the spread profits from this decay
          differential. The strategy also benefits from positive contango — when short-term IV is lower
          than long-term IV, which is the normal state of the options market.
        </Prose>

        <Subheading>Entry criteria</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Market regime: NEUTRAL only</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> IV environment: LOW or MODERATE (IVR &lt; 50)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Contango: must be NORMAL (short-term IV &lt; long-term IV)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> ORATS IV forecast: FAIR or UNDERVALUED preferred (buying the long leg)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Strike: ATM (50 delta)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Short leg DTE: 20–35 days</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Long leg DTE: 50–90 days (at least 30 days after short leg)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Net debit ≤ $2.50</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Earnings must NOT fall between the two expirations</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Stock must be range-bound (within Bollinger Bands)</li>
        </ul>

        <div
          className="mt-3 p-3 rounded text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
            color: 'var(--text-secondary)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <strong>Why this strategy?</strong> Calendar Spread fills a gap in the strategy lineup: it's the only strategy that enters in NEUTRAL regime + LOW/MODERATE IV. Iron Condor and Iron Butterfly require HIGH IV, the Wheel skews toward moderate IV with directional bias via assignment, and the credit/debit spreads all require a directional view. When the market is quiet <em>and</em> options are cheap, Calendar Spread is the only thing that runs — it's also the bot's only vega-positive strategy, so it profits from IV expansion rather than contraction.
        </div>

        <Subheading>Risk profile</Subheading>
        <div className="grid gap-3 md:grid-cols-3">
          <SubCard title="Max profit">
            Achieved when stock is exactly at the strike at the short option's expiration.
          </SubCard>
          <SubCard title="Max loss">
            Net debit paid. Occurs if stock moves significantly away from the strike in either direction.
          </SubCard>
          <SubCard title="Key risk">
            Narrow profit zone. Any significant directional move loses money. Pure time-decay play.
          </SubCard>
        </div>

        <Subheading>How the bot manages this position</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Profit target:</strong> close at 50% gain on debit</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Stop loss:</strong> close at 50% loss of debit</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Short leg DTE ≤ 7:</strong> roll to next monthly (net credit only)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Max 2 rolls</strong> of the short leg — then close</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Stock moves &gt; 1 ATR from strike:</strong> close (directional thesis broken)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> <strong>Short leg delta &gt; 0.70:</strong> close (deep ITM, assignment risk)</li>
        </ul>

        <div style={{
          marginTop: '12px',
          padding: '12px',
          borderRadius: '8px',
          backgroundColor: 'color-mix(in srgb, var(--teal, #14B8A6) 8%, transparent)',
          color: 'var(--text-secondary)',
          borderLeft: '3px solid var(--teal, #14B8A6)',
          fontSize: '13px',
        }}>
          <strong>Why contango matters:</strong> Calendar spreads have a structural edge when contango
          is positive because the short option (near-term) decays faster than the long option (far-term).
          If contango flips to backwardation, this edge disappears — the bot blocks calendar entries in
          that environment.
        </div>
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
                <th className="px-4 py-3 font-medium">ORATS Signal</th>
              </tr>
            </thead>
            <tbody>
              {ROUTING_ROWS.map((r) => (
                <tr key={r.regime + r.iv + r.strategy} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                  <td className="px-4 py-3 font-mono text-xs">{r.regime}</td>
                  <td className="px-4 py-3 font-mono text-xs">{r.iv}</td>
                  <td className="px-4 py-3" style={{ color: 'var(--text-primary)' }}>{r.strategy}</td>
                  <td className="px-4 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                    {r.note || '—'}
                  </td>
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

        <Subheading>ORATS Volatility Intelligence</Subheading>
        <Prose>
          Every decision now incorporates signals from ORATS, a professional options analytics platform.
          These signals help Claude determine not just whether to trade, but whether the specific options
          are fairly priced for the strategy being considered.
        </Prose>
        <div className="grid gap-3 md:grid-cols-2">
          <SubCard title="IV Forecast">
            ORATS forecasts where IV will be in 20 days. When current IV exceeds the forecast (OVERVALUED),
            options are expensive — ideal for selling premium. When IV is below the forecast (UNDERVALUED),
            options are cheap — ideal for buying. Credit strategies require OVERVALUED or FAIR; the long
            call vertical requires UNDERVALUED or FAIR.
          </SubCard>
          <SubCard title="Slope Percentile">
            Measures where the current skew steepness sits in its 1-year range. Above 66th percentile
            means puts are expensive relative to history — a tailwind for bull put spreads and iron condors.
            Below 33rd percentile means puts are cheap — less edge in selling put spreads.
          </SubCard>
          <SubCard title="Contango">
            Measures short-term IV vs long-term IV. Normal contango (short-term lower) is healthy. When
            the term structure flips to backwardation (short-term higher), it signals near-term fear — the
            bot blocks iron condor entries and factors it into regime assessment.
          </SubCard>
          <SubCard title="Spread Yield">
            Credit received divided by stock price — normalizes premium across different price levels. A
            $0.75 credit on a $530 stock is very different from $0.75 on a $200 stock. The bot now uses
            spread yield (minimum 0.1%) instead of a fixed dollar floor to evaluate credit quality.
          </SubCard>
        </div>

        <Subheading>The three strategies</Subheading>
        <div className="grid gap-3 md:grid-cols-3">
          <SubCard
            title="Bull Put Spread (Bullish / Neutral)"
            bullets={[
              'Max loss capped at spread width minus credit received',
              'Used in MODERATE or HIGH IV, NEUTRAL or BULL regime',
              'DTE: 21–35 days',
              'Spread yield ≥ 0.1% of stock price (normalized credit)',
              'Credit-to-width ratio ≥ 15%',
              'Prefers entry when ORATS IV forecast confirms options are overvalued',
              'Slope percentile > 66 = puts expensive → extra edge',
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
              'Spread yield ≥ 0.1% of stock price',
              'Ex-dividend and earnings dates checked before entry',
              'Blocked when ORATS IV forecast says options are undervalued',
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
              "Blocked when ORATS IV forecast says options are overvalued (don't overpay for long calls)",
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
        <div className="grid gap-3 md:grid-cols-3 mt-3">
          <SubCard title="Position Sizing Caps">
            <ul className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Standard Wheel: max 10% of buying power per CSP, max 5 concurrent positions</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Turnover Wheel: max 5% of buying power per CSP, max 10 concurrent positions</li>
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
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Circuit breaker monitors aggregate equity across all four accounts</li>
            </ul>
          </SubCard>
          <SubCard title="Macro Event Block">
            <ul className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Blocks new entries the day of and the trading day before FOMC / CPI / NFP</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Existing positions manage normally through events</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Event schedule populated from <code className="font-mono">data/macro_events.json</code></li>
            </ul>
          </SubCard>
          <SubCard title="Anti-Crowding Across Accounts">
            <ul className="space-y-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Before opening a new position, the bot checks whether the same directional-risk family (short-put, short-call, long-directional) is already open on that underlying in any other account</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Blocks correlated stacking: e.g. a bull put spread on AAPL prevents a new wheel CSP on AAPL and vice versa</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Iron condors block both sides (short-put and short-call family) on the same underlying</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Exception: Standard Wheel and Turnover Wheel are allowed to coexist on the same underlying — this is the comparative-experiment design</li>
              <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Management actions (roll, close) are never blocked — the check only fires on net-new entries</li>
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
