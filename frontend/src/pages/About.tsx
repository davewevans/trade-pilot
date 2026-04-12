// Logo: only `public/favicon.svg` exists in the project's static assets today.
// If a higher-resolution logo (e.g. /logo.svg or /logo.png) is added later,
// swap the `src` below to point at it.

type Step = { num: number; label: string; body: string }

const STEPS: Step[] = [
  {
    num: 1,
    label: 'Gather',
    body:
      'Fetches live prices, IV rank, macro data, earnings dates, and account state from five data sources every cycle.',
  },
  {
    num: 2,
    label: 'Analyze',
    body:
      'Classifies market regime and IV environment, runs pre-condition checks, and assembles a structured context package.',
  },
  {
    num: 3,
    label: 'Decide',
    body:
      'Sends the full context to Claude with a strategy-specific prompt. Claude returns a structured JSON recommendation — trade, hold, or skip.',
  },
  {
    num: 4,
    label: 'Execute',
    body:
      'Validates the recommendation against hard guardrails, then places a limit order through Alpaca if everything passes. Logs the result either way.',
  },
]

const ACCOUNTS = [
  {
    accent: 'var(--accent-wheel, var(--accent))',
    name: 'Wheel Account',
    body:
      'Runs the wheel strategy — selling cash-secured puts for income and covered calls after assignment. Cycles through four states: IDLE, SHORT_PUT, LONG_STOCK, SHORT_CALL.',
  },
  {
    accent: 'var(--accent-iron-condor, var(--accent))',
    name: 'Iron Condor Account',
    body:
      'Runs a single strategy: the iron condor. Sells an OTM put spread and an OTM call spread simultaneously, profiting when the market stays within a defined range. Activated in neutral, high-IV conditions.',
  },
  {
    accent: 'var(--accent-spreads, var(--accent))',
    name: 'Spreads Account',
    body:
      'Runs three strategies adaptively — Bull Put Spread, Bear Call Spread, and Long Call Vertical — switching between them based on the current market regime and IV environment. Only one new position per cycle.',
  },
]

const GOALS = [
  {
    title: 'Generate consistent income',
    body:
      'The primary objective is to collect options premium systematically using high-probability, defined-risk strategies across multiple market environments.',
  },
  {
    title: 'Stay disciplined',
    body:
      "Every decision goes through the same structured process. No emotional overrides, no chasing trades, no rule exceptions. Skipping when conditions aren't right is a feature, not a bug.",
  },
  {
    title: 'Learn and improve',
    body:
      'All decisions, skip reasons, and outcomes are logged to a trade journal. The system is designed to be analyzed and refined over time based on actual performance data.',
  },
  {
    title: 'Manage risk first',
    body:
      'Position sizing limits, earnings avoidance, circuit breakers, and per-trade guardrails exist specifically to protect capital. The bot is designed to survive bad streaks, not just exploit good ones.',
  },
]

const STACK = [
  { label: 'AI', value: 'Claude Sonnet (Anthropic)' },
  { label: 'Broker', value: 'Alpaca (paper trading)' },
  { label: 'Language', value: 'Python 3.11+' },
  { label: 'Data', value: 'Alpaca Market Data, yfinance, FRED, Finnhub, ORATS' },
  { label: 'Scheduler', value: 'Python schedule library' },
  { label: 'API', value: 'FastAPI' },
  { label: 'Dashboard', value: 'React' },
  { label: 'Deployment', value: 'Render.com' },
]

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h2
      className="text-xs uppercase tracking-wider font-semibold mb-4"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

export function About() {
  return (
    <div className="max-w-5xl mx-auto">
      {/* Logo + headline */}
      <div className="flex flex-col items-center text-center pt-4 pb-10">
        <img
          src="/favicon.svg"
          alt="trade-pilot"
          width={96}
          height={96}
          className="mb-5"
        />
        <h1 className="text-3xl font-semibold tracking-wide" style={{ color: 'var(--text-primary)' }}>
          trade-pilot
        </h1>
        <p className="text-sm italic mt-1" style={{ color: 'var(--text-secondary)' }}>
          An AI-powered options trading bot
        </p>
      </div>

      {/* Section 1: What It Is */}
      <section className="mb-12">
        <SectionHeader>What It Is</SectionHeader>
        <div
          className="p-5 rounded-lg border"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            trade-pilot is a fully automated options trading system built to research, decide, and
            execute options trades without manual intervention. It connects to live market data,
            runs it through a structured analysis pipeline, asks Claude — Anthropic's AI — to make
            a trading recommendation, and then validates and executes that recommendation through
            the Alpaca brokerage API. Every decision is logged, every skip is explained, and every
            trade is traceable.
          </p>
        </div>
      </section>

      {/* Section 2: How It Works */}
      <section className="mb-12">
        <SectionHeader>How It Works</SectionHeader>
        <div className="grid gap-3 md:grid-cols-4">
          {STEPS.map((s) => (
            <div
              key={s.num}
              className="p-4 rounded-lg border"
              style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
            >
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-mono font-semibold"
                  style={{
                    backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
                    color: 'var(--accent)',
                  }}
                >
                  {s.num}
                </span>
                <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                  {s.label}
                </h3>
              </div>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {s.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Section 3: Three Accounts */}
      <section className="mb-12">
        <SectionHeader>The Three Accounts</SectionHeader>
        <div className="grid gap-3 md:grid-cols-3">
          {ACCOUNTS.map((a) => (
            <div
              key={a.name}
              className="p-4 rounded-lg border"
              style={{
                backgroundColor: 'var(--bg-card)',
                borderColor: 'var(--border)',
                borderTop: `3px solid ${a.accent}`,
              }}
            >
              <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
                {a.name}
              </h3>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {a.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Section 4: Goals */}
      <section className="mb-12">
        <SectionHeader>Goals</SectionHeader>
        <div className="grid gap-3 md:grid-cols-2">
          {GOALS.map((g) => (
            <div
              key={g.title}
              className="p-4 rounded-lg border"
              style={{
                backgroundColor: 'var(--bg-card)',
                borderColor: 'var(--border)',
                borderLeft: '3px solid var(--accent)',
              }}
            >
              <h3 className="font-semibold text-sm mb-1.5" style={{ color: 'var(--text-primary)' }}>
                {g.title}
              </h3>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {g.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Section 5: Tech Stack */}
      <section className="mb-12">
        <SectionHeader>Tech Stack</SectionHeader>
        <div
          className="rounded-lg border divide-y md:divide-y-0 md:grid md:grid-cols-2"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          {STACK.map((s, i) => (
            <div
              key={s.label}
              className="px-4 py-3 flex items-baseline gap-3"
              style={{
                borderColor: 'var(--border)',
                borderTop: i >= 2 ? '1px solid var(--border)' : undefined,
              }}
            >
              <span
                className="text-[10px] uppercase tracking-wider font-semibold w-24 shrink-0"
                style={{ color: 'var(--text-muted)' }}
              >
                {s.label}
              </span>
              <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
                {s.value}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* Footer note */}
      <div className="text-center text-xs pb-8" style={{ color: 'var(--text-muted)' }}>
        Currently running on paper trading accounts. No real capital is at risk.
      </div>
    </div>
  )
}
