function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-xs uppercase tracking-wider font-semibold mt-8 mb-3"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

function Card({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div
      className="p-4 rounded-lg border h-full"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h3>
      <div className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </div>
    </div>
  )
}

type Regime = {
  emoji: string
  name: string
  color: string
  trigger: React.ReactNode
  behavior: string
  context: string
}

const REGIMES: Regime[] = [
  {
    emoji: '🔴',
    name: 'CRASH',
    color: 'var(--red)',
    trigger: <>VIX ≥ 35 (highest priority — overrides all other signals)</>,
    behavior:
      'No new positions on any account. All strategies are management-only. Existing positions are still monitored.',
    context:
      'A market event has caused a spike in fear. A crash, a Fed shock, a geopolitical event. Premiums are very rich but risk is unquantifiable. The bot steps aside entirely.',
  },
  {
    emoji: '🟠',
    name: 'BEAR',
    color: '#f97316',
    trigger: (
      <>
        Either: VIX ≥ 25 AND SPX below 200-day SMA<br />
        Or: VIX ≥ 20 AND SPX below 50-day SMA
      </>
    ),
    behavior:
      'Only Bear Call Spreads are eligible for new entries on the Spreads account. The Wheel avoids new CSPs — selling puts into a falling market carries high assignment risk. Iron Condor paused.',
    context:
      'The market is in a downtrend with elevated fear. Selling calls above resistance makes more sense than selling puts below falling support.',
  },
  {
    emoji: '🟡',
    name: 'NEUTRAL',
    color: 'var(--yellow, #eab308)',
    trigger: <>None of the CRASH, BEAR, BULL, or EUPHORIA conditions are met. This is the default.</>,
    behavior:
      'Standard conditions. Wheel runs normally. Iron Condor is eligible if IV is HIGH. Bull Put Spread is the primary spread.',
    context: 'A sideways or mildly trending market with no strong signal in either direction.',
  },
  {
    emoji: '🟢',
    name: 'BULL',
    color: 'var(--green)',
    trigger: <>VIX ≤ 18 AND SPX above its 50-day SMA</>,
    behavior:
      'Favorable for Wheel (CSPs and covered calls). Bull Put Spread is primary spread. If IV is also LOW, Long Call Verticals become eligible — the only debit strategy the bot runs. Covered calls are placed higher to give stock room to run.',
    context:
      'A calm, uptrending market. Assignment risk is lower, trends are supportive, and premium-selling conditions are solid.',
  },
  {
    emoji: '⚡',
    name: 'EUPHORIA',
    color: '#a855f7',
    trigger: <>VIX ≤ 14 AND SPX above its 50-day SMA AND Fear &amp; Greed ≥ 75</>,
    behavior:
      'Late-cycle caution. Position sizing is reduced by 25%. The bot prefers defined-risk spreads over naked CSPs. New entries are more selective.',
    context:
      'Everything is going up and sentiment is stretched. Historically when markets feel most unstoppable, reversals can be sudden and sharp.',
  },
]

function RegimeCard({ r }: { r: Regime }) {
  return (
    <div
      className="p-4 rounded-lg border h-full"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderLeft: `4px solid ${r.color}`,
      }}
    >
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg leading-none">{r.emoji}</span>
        <h3 className="font-semibold tracking-wide" style={{ color: r.color }}>
          {r.name}
        </h3>
      </div>
      <div className="space-y-2 text-sm">
        <div>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
            Trigger
          </div>
          <div style={{ color: 'var(--text-secondary)' }}>{r.trigger}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
            Bot behavior
          </div>
          <div style={{ color: 'var(--text-secondary)' }}>{r.behavior}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
            Context
          </div>
          <div style={{ color: 'var(--text-secondary)' }}>{r.context}</div>
        </div>
      </div>
    </div>
  )
}

function StabilityWindow({
  title,
  readings,
  result,
  resultColor,
}: {
  title: string
  readings: string[]
  result: string
  resultColor: string
}) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div className="text-xs mb-3 font-semibold" style={{ color: 'var(--text-primary)' }}>
        {title}
      </div>
      <div className="flex gap-2 mb-3">
        {readings.map((r, i) => (
          <div
            key={i}
            className="flex-1 py-2 text-center text-xs font-mono rounded"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border)',
            }}
          >
            {r}
          </div>
        ))}
      </div>
      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Result: <span style={{ color: resultColor, fontWeight: 600 }}>{result}</span>
      </div>
    </div>
  )
}

const IV_ENV_ROWS = [
  { range: 'Below 30', env: 'LOW — options are cheap' },
  { range: '30 to 50', env: 'MODERATE — normal conditions' },
  { range: 'Above 50', env: 'HIGH — options are expensive' },
]

const ROUTING_ROWS = [
  { regime: 'NEUTRAL', iv: 'HIGH', strategy: 'Iron Condor' },
  { regime: 'NEUTRAL', iv: 'MODERATE', strategy: 'Bull Put Spread' },
  { regime: 'BULL', iv: 'LOW', strategy: 'Long Call Vertical' },
  { regime: 'BULL', iv: 'MODERATE or HIGH', strategy: 'Bull Put Spread' },
  { regime: 'BEAR', iv: 'Any', strategy: 'Bear Call Spread' },
  { regime: 'CRASH', iv: 'Any', strategy: 'No new positions' },
  { regime: 'EUPHORIA', iv: 'Any', strategy: 'Bull Put Spread (reduced size)' },
]

export function MarketRegimes() {
  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          Market Regimes
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          How the bot classifies market conditions and routes strategy selection.
        </p>
      </div>

      {/* Section 1 */}
      <SectionHeader>What Is a Market Regime?</SectionHeader>
      <div
        className="p-4 rounded-lg border"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Before asking Claude anything, the bot reads three market-wide signals and classifies the
          current environment into one of five regimes: BULL, NEUTRAL, BEAR, CRASH, or EUPHORIA.
          This classification determines which strategies are allowed to open new positions that
          cycle. It is not a price prediction — it is a snapshot of current conditions used to keep
          the bot aligned with the market it is actually operating in.
        </p>
      </div>

      {/* Section 2 */}
      <SectionHeader>The Three Signals</SectionHeader>
      <div className="grid gap-3 md:grid-cols-3">
        <Card title="VIX — The Fear Gauge">
          <p className="mb-2">
            The CBOE Volatility Index measures how much volatility the options market is pricing
            into the S&P 500 over the next 30 days. A low VIX means calm is expected. A high VIX
            means large moves are expected. The bot reads VIX live every cycle. It is the
            highest-priority signal: VIX ≥ 35 immediately triggers CRASH regardless of anything else.
          </p>
          <ul className="text-xs space-y-1 mt-3">
            <li>• <span className="font-mono">&lt; 15</span> — Very calm, thin premiums</li>
            <li>• <span className="font-mono">15–18</span> — Normal, good selling conditions</li>
            <li>• <span className="font-mono">18–25</span> — Elevated, more risk</li>
            <li>• <span className="font-mono">25–35</span> — High fear, tighten sizing</li>
            <li>• <span className="font-mono">≥ 35</span> — Extreme — CRASH regime</li>
          </ul>
        </Card>
        <Card title="SPX Trend — 50-day and 200-day SMA">
          <p className="mb-2">
            The bot checks whether the S&P 500 is trading above or below its 50-day and 200-day
            simple moving averages. The bot uses SPX specifically (not individual watchlist stocks)
            so regime reflects the broad market.
          </p>
          <ul className="text-xs space-y-1 mt-3">
            <li>• Above both SMAs: uptrend, favorable for bullish strategies</li>
            <li>• Below 50-day: early warning sign</li>
            <li>• Below 200-day: more serious deterioration</li>
          </ul>
        </Card>
        <Card title="Fear & Greed Index — 0 to 100">
          <p className="mb-2">
            A composite sentiment score combining seven market signals including momentum,
            put/call ratios, and market breadth. Updated daily. Used primarily to detect EUPHORIA.
          </p>
          <ul className="text-xs space-y-1 mt-3">
            <li>• <span className="font-mono">0–25</span> — Extreme Fear</li>
            <li>• <span className="font-mono">25–45</span> — Fear</li>
            <li>• <span className="font-mono">45–55</span> — Neutral</li>
            <li>• <span className="font-mono">55–75</span> — Greed</li>
            <li>• <span className="font-mono">75–100</span> — Extreme Greed</li>
          </ul>
        </Card>
      </div>

      {/* Section 3 */}
      <SectionHeader>The Five Regimes</SectionHeader>
      <div className="grid gap-3 md:grid-cols-2">
        {REGIMES.map((r) => (
          <RegimeCard key={r.name} r={r} />
        ))}
      </div>

      {/* Section 4 */}
      <SectionHeader>The Stability Filter — Why Regimes Don't Flip Daily</SectionHeader>
      <div
        className="p-4 rounded-lg border mb-4"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 6%, var(--bg-card))',
          borderColor: 'var(--border)',
          borderLeft: '3px solid var(--accent)',
        }}
      >
        <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text-secondary)' }}>
          The bot does not act on a regime the instant it is detected. It requires 3 consecutive
          identical readings before confirming a change.
        </p>
        <ul className="text-sm space-y-1" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> Each cycle, the bot calculates the raw regime from live signals</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> That reading is added to a rolling 3-reading window</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> The confirmed regime only changes when all 3 readings agree</li>
          <li className="flex gap-2"><span style={{ color: 'var(--accent)' }}>•</span> If the window is mixed, the confirmed regime stays unchanged</li>
        </ul>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <StabilityWindow
          title="Example A — mixed window"
          readings={['BULL', 'NEUTRAL', 'BULL']}
          result="No change — confirmed regime stays at previous value"
          resultColor="var(--text-muted)"
        />
        <StabilityWindow
          title="Example B — unanimous window"
          readings={['BULL', 'BULL', 'BULL']}
          result="Confirmed → BULL"
          resultColor="var(--green)"
        />
      </div>

      {/* Section 5 */}
      <SectionHeader>How IV Environment Modifies Routing</SectionHeader>
      <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
        Alongside regime, the bot classifies the IV environment as LOW, MODERATE, or HIGH based on
        the median IV rank across the watchlist. This refines which spread strategy is selected.
      </p>

      <div
        className="rounded-lg border overflow-x-auto mb-4"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              <th className="px-4 py-3 font-medium">Median IV Rank</th>
              <th className="px-4 py-3 font-medium">IV Environment</th>
            </tr>
          </thead>
          <tbody>
            {IV_ENV_ROWS.map((r) => (
              <tr key={r.range} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                <td className="px-4 py-3 font-mono text-xs">{r.range}</td>
                <td className="px-4 py-3">{r.env}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div
        className="rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
              <th className="px-4 py-3 font-medium">Regime</th>
              <th className="px-4 py-3 font-medium">IV Environment</th>
              <th className="px-4 py-3 font-medium">Strategy Selected</th>
            </tr>
          </thead>
          <tbody>
            {ROUTING_ROWS.map((r, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                <td className="px-4 py-3 font-mono text-xs">{r.regime}</td>
                <td className="px-4 py-3 font-mono text-xs">{r.iv}</td>
                <td className="px-4 py-3" style={{ color: 'var(--text-primary)' }}>{r.strategy}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs mt-3" style={{ color: 'var(--text-muted)' }}>
        The Wheel and Iron Condor accounts are not affected by this routing table — they run their
        own internal checks independently of the Spreads account routing logic.
      </p>
    </div>
  )
}
