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
  { regime: 'NEUTRAL or BULL', iv: 'HIGH', strategy: 'Iron Condor*' },
  { regime: 'NEUTRAL or BULL', iv: 'MODERATE', strategy: 'Bull Put Spread' },
  { regime: 'BEAR', iv: 'MODERATE or HIGH', strategy: 'Bear Call Spread' },
  { regime: 'BULL', iv: 'LOW', strategy: 'Long Call Vertical' },
  { regime: 'CRASH', iv: 'Any', strategy: 'No new positions' },
]

export function Strategies() {
  return (
    <div className="max-w-5xl">
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

        <Subheading>How the bot manages positions</Subheading>
        <ul className="space-y-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> If a position moves against us (delta doubles), the bot rolls to a better strike.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> If we've captured 50% of the premium, the bot closes early to take profit.</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> If expiration is within 7 days and the position is risky, the bot rolls out.</li>
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
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> High IV environment (volatility is expensive, we sell it)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Neutral market regime (no strong directional trend)</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Earnings must be &gt; 30 days away</li>
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
        <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
          *Iron Condor here uses the spreads account logic; the dedicated Iron Condor account runs independently.
        </p>
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
              'Max loss capped (spread width minus premium)',
              'Used in moderate IV, neutral-to-bullish conditions',
              'DTE: 20–45 days',
            ]}
          >
            We sell a put and buy a cheaper put below it. We profit if the stock stays above our short
            strike. The long put below limits our maximum loss. A defined-risk way to collect premium when
            we're neutral to slightly bullish.
          </SubCard>
          <SubCard
            title="Bear Call Spread (Bearish / Neutral)"
            bullets={[
              'Mirror image of the bull put spread, on the call side',
              'Used in moderate-to-high IV, bearish conditions',
              'DTE: 20–45 days',
              'Ex-dividend dates checked to avoid early assignment',
            ]}
          >
            We sell a call and buy a cheaper call above it. We profit if the stock stays below our short
            strike. Used when we expect the market to be flat to slightly bearish.
          </SubCard>
          <SubCard
            title="Long Call Vertical (Bullish, Low IV)"
            bullets={[
              'Debit strategy — we pay premium upfront',
              'Profit and risk both capped',
              'Only deployed in BULL regime + LOW IV (very selective)',
              'DTE: 25–65 days',
            ]}
          >
            Unlike the other two, this is a debit strategy — we pay to open it. We buy a call and sell a
            higher-strike call above it. We profit if the stock rises above our long strike. Used
            specifically when IV is low (options are cheap) and the market is clearly bullish.
          </SubCard>
        </div>
      </AccountSection>
    </div>
  )
}
