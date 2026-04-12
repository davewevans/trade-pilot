type Rule = {
  title: string
  body: string
  badge?: string
}

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
      className="text-xs uppercase tracking-wider font-semibold mt-8 mb-3"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

function RuleCard({ rule }: { rule: Rule }) {
  return (
    <div
      className="p-4 rounded-lg border h-full flex flex-col"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
          {rule.title}
        </h3>
        {rule.badge && (
          <span
            className="text-[10px] px-2 py-0.5 rounded-full whitespace-nowrap font-mono"
            style={{
              backgroundColor: 'color-mix(in srgb, var(--accent) 18%, transparent)',
              color: 'var(--accent)',
            }}
          >
            {rule.badge}
          </span>
        )}
      </div>
      <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {rule.body}
      </p>
    </div>
  )
}

const UNIVERSAL_RULES: Rule[] = [
  {
    title: 'Limit Orders Only',
    badge: 'No market orders',
    body:
      'The bot never places market orders. Every options trade is a limit order, meaning it will only execute at a price we specify or better. This prevents getting a bad fill in a fast-moving market.',
  },
  {
    title: 'One Contract at a Time',
    badge: 'qty = 1',
    body:
      'The bot always trades exactly 1 contract per decision. This keeps position sizes predictable and manageable.',
  },
  {
    title: 'No Trading During Earnings',
    badge: 'Per-strategy',
    body:
      "The bot checks how many days until a company's next earnings report. If earnings are too close, it skips the trade entirely. Each strategy has its own minimum distance (see below).",
  },
  {
    title: 'Symbol Validation',
    badge: 'OCC format',
    body:
      'Every option symbol must pass a strict format check (OCC standard format) before the order is placed. Malformed symbols are rejected automatically.',
  },
]

type StrategyRow = {
  strategy: string
  earnings: string
  dte: string
  size: string
  other: string
}

const STRATEGY_ROWS: StrategyRow[] = [
  {
    strategy: 'Wheel (CSP)',
    earnings: '> 14 days required',
    dte: '21–35 days',
    size: '10% of buying power',
    other: 'One CSP per symbol max',
  },
  {
    strategy: 'Wheel (CC)',
    earnings: '> 21 days required',
    dte: '21–35 days',
    size: '—',
    other: 'Strike must be above cost basis',
  },
  {
    strategy: 'Iron Condor',
    earnings: '> 30 days required',
    dte: '20–50 days',
    size: '5% of buying power per trade',
    other: 'One condor per symbol max',
  },
  {
    strategy: 'Bull Put Spread',
    earnings: '> 21 days required',
    dte: '20–45 days',
    size: '2% of buying power per trade',
    other: 'One spread per symbol max',
  },
  {
    strategy: 'Bear Call Spread',
    earnings: '> 21 days required',
    dte: '20–45 days',
    size: '2% of buying power per trade',
    other: 'Ex-dividend date checked',
  },
  {
    strategy: 'Long Call Vertical',
    earnings: 'within DTE window',
    dte: '25–65 days',
    size: '1% of buying power per trade',
    other: 'BULL regime only; LOW IV only',
  },
]

const CIRCUIT_RULES: Rule[] = [
  {
    title: 'Daily Loss Limit',
    badge: 'Per-day',
    body:
      'If the portfolio loses too much in a single day, the bot stops opening new positions for the rest of the day. Existing positions are still managed.',
  },
  {
    title: 'Portfolio Drawdown',
    badge: 'From peak',
    body:
      'If the portfolio drops significantly from its peak value, the bot shifts to conservative mode or halts entirely.',
  },
]

function CircuitColorCard({
  emoji,
  color,
  name,
  description,
}: {
  emoji: string
  color: string
  name: string
  description: string
}) {
  return (
    <div
      className="p-4 rounded-lg border flex gap-3 items-start"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <div className="text-2xl leading-none">{emoji}</div>
      <div>
        <div className="font-semibold text-sm" style={{ color }}>
          {name}
        </div>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          {description}
        </p>
      </div>
    </div>
  )
}

export function Guardrails() {
  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Guardrails"
        subtitle="Safety rules that protect the portfolio. These are enforced in code — Claude cannot override them."
      />

      <SectionHeader>Universal Rules</SectionHeader>
      <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
        Apply to every strategy, every account, every trade.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {UNIVERSAL_RULES.map((r) => (
          <RuleCard key={r.title} rule={r} />
        ))}
      </div>

      <SectionHeader>Per-Strategy Rules</SectionHeader>
      <div
        className="rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr style={{ color: 'var(--text-muted)' }} className="text-left text-xs uppercase tracking-wider">
              <th className="px-4 py-3 font-medium">Strategy</th>
              <th className="px-4 py-3 font-medium">Earnings Block</th>
              <th className="px-4 py-3 font-medium">DTE Range</th>
              <th className="px-4 py-3 font-medium">Max Trade Size</th>
              <th className="px-4 py-3 font-medium">Other</th>
            </tr>
          </thead>
          <tbody>
            {STRATEGY_ROWS.map((row, i) => (
              <tr
                key={row.strategy}
                style={{
                  borderTop: i === 0 ? `1px solid var(--border)` : `1px solid var(--border)`,
                  color: 'var(--text-secondary)',
                }}
              >
                <td className="px-4 py-3 font-medium" style={{ color: 'var(--text-primary)' }}>
                  {row.strategy}
                </td>
                <td className="px-4 py-3">{row.earnings}</td>
                <td className="px-4 py-3 font-mono text-xs">{row.dte}</td>
                <td className="px-4 py-3">{row.size}</td>
                <td className="px-4 py-3">{row.other}</td>
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
        These rules are enforced in Python code — they cannot be overridden by the AI's recommendation.
        If Claude suggests a trade that violates any of these rules, the trade is automatically rejected and logged.
      </div>

      <SectionHeader>Circuit Breakers</SectionHeader>
      <div className="grid gap-3 md:grid-cols-2 mb-4">
        {CIRCUIT_RULES.map((r) => (
          <RuleCard key={r.title} rule={r} />
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <CircuitColorCard
          emoji="🟢"
          color="var(--green)"
          name="GREEN"
          description="Normal operation. New positions allowed."
        />
        <CircuitColorCard
          emoji="🟡"
          color="var(--yellow, #eab308)"
          name="YELLOW"
          description="Management only. No new positions opened; existing positions still managed."
        />
        <CircuitColorCard
          emoji="🔴"
          color="var(--red)"
          name="RED"
          description="All new positions halted. Existing positions still managed."
        />
      </div>
    </div>
  )
}
