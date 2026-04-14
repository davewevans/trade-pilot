import { useRef } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

type Step = {
  num: number
  label: string
  oneLine: string
  body: string
}

const STEPS: Step[] = [
  {
    num: 1,
    label: 'Scheduler Triggers',
    oneLine: 'Market open (10 AM) or position check (10:45, 11:30, 12:30, 2 PM ET)',
    body:
      'A background scheduler runs the bot on a fixed timetable. Nothing happens unless one of these times fires — there is no real-time event-driven trading. The pre-market job runs once at open; intraday checks run a few times during the session to manage existing positions and look for new entries.',
  },
  {
    num: 2,
    label: 'Circuit Breaker Check',
    oneLine: 'Is the bot halted? Is there a daily/weekly loss breach?',
    body:
      'Before any other work happens, the bot checks the portfolio-level safety status. If a halt is active (RED status, weekly loss breach, drawdown lock), the cycle stops here for new entries — but existing positions are still managed.',
  },
  {
    num: 3,
    label: 'Build Market Context',
    oneLine: 'Alpaca + yfinance + FRED + Finnhub + ORATS + CNN Fear & Greed → one JSON package',
    body:
      "Before asking Claude anything, the bot collects data from six different sources and combines it into a single snapshot of current market conditions. This includes current stock prices, how volatile options are right now compared to recent history (IV rank), what the broader market is doing (VIX, Fear & Greed), and when each company's next earnings report is scheduled. Claude sees all of this — not just the price.",
  },
  {
    num: 4,
    label: 'Determine Market Regime',
    oneLine: 'VIX + SPX trend + Fear & Greed → BULL / NEUTRAL / BEAR / CRASH / EUPHORIA',
    body:
      'The bot blends a few macro indicators into a single label that describes the overall mood of the market, plus a separate label for whether options premiums are cheap, average, or expensive (LOW / MODERATE / HIGH). These two labels are the main inputs that decide which strategies are eligible to run.',
  },
  {
    num: 5,
    label: 'Strategy Router',
    oneLine: 'Decides which strategies run this cycle',
    body:
      'Based on the regime, the IV environment, and the circuit breaker status, the router decides which strategies are even worth evaluating. For example: in a BULL + LOW IV regime, the Long Call Vertical becomes eligible; in a CRASH regime, nothing new opens at all.',
  },
  {
    num: 6,
    label: 'Pre-condition Check',
    oneLine: 'Per-strategy hard filters — failure means SKIP without calling Claude',
    body:
      "Each strategy has a list of hard filters it checks itself, before spending money on a Claude API call. Earnings windows, IV thresholds, existing positions, allocation caps — all checked here. For spread strategies in IDLE state, the bot pre-checks every symbol in the strategy's watchlist, scores the qualifying candidates, and only calls Claude once for the single best setup. If no symbol passes, the entire strategy skips. Claude is never bothered with trades that obviously can't happen.",
  },
  {
    num: 7,
    label: 'Claude Makes a Decision',
    oneLine: 'Strategy-specific prompt + context → structured JSON recommendation',
    body:
      'The full context package is sent to Claude with a prompt tailored to the specific strategy phase. Claude responds with structured JSON: action, symbol, strike, expiration, limit price, and detailed reasoning across macro, fundamental, technical, volatility, selection, and risk dimensions. The schema is enforced — malformed responses are rejected and retried once before falling back to SKIP.',
  },
  {
    num: 8,
    label: 'Guardrails Validate',
    oneLine: "Python validates Claude's recommendation against hard rules",
    body:
      "Even after Claude returns a recommendation, Python code runs one more set of checks: DTE within range, earnings far enough away, position size within caps, OCC symbol format valid, no duplicate positions on the same symbol. Claude's recommendation cannot bypass these — if any check fails, the trade is rejected and the reason is logged.",
  },
  {
    num: 9,
    label: 'Execute or Log',
    oneLine: 'Valid trade → limit order via Alpaca. Otherwise → logged with reason.',
    body:
      'Valid trades are submitted as limit orders through the Alpaca API. Skips and rejections are written to the trade journal with the exact reason so the dashboard can show what happened and why. Either way, the dashboard snapshot files are updated so the UI reflects the latest state.',
  },
]

function StepBadge({ n }: { n: number }) {
  return (
    <div
      className="w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-sm font-mono font-semibold"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--accent) 20%, transparent)',
        color: 'var(--accent)',
        border: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
      }}
    >
      {n}
    </div>
  )
}

function FlowNode({ step }: { step: Step }) {
  return (
    <div
      className="p-3 rounded-lg border flex gap-3 items-start"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <StepBadge n={step.num} />
      <div className="min-w-0">
        <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
          {step.label}
        </div>
        <div className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
          {step.oneLine}
        </div>
      </div>
    </div>
  )
}

function Arrow() {
  return (
    <div className="flex justify-center my-1" aria-hidden="true">
      <span style={{ color: 'var(--text-muted)' }}>↓</span>
    </div>
  )
}

export function HowItWorks() {
  const contentRef = useRef<HTMLDivElement>(null)
  return (
    <div ref={contentRef} className="max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          How Claude Decides
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          The full decision loop, end to end. Nothing skipped.
        </p>
      </div>

      {/* Flow diagram */}
      <div
        className="p-5 rounded-lg border mb-10"
        style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
      >
        {STEPS.map((s, i) => (
          <div key={s.num}>
            <FlowNode step={s} />
            {i < STEPS.length - 1 && <Arrow />}
          </div>
        ))}
      </div>

      {/* Detailed paragraphs */}
      <h2
        className="text-xs uppercase tracking-wider font-semibold mb-4"
        style={{ color: 'var(--text-muted)' }}
      >
        What This Means
      </h2>
      <div className="space-y-4">
        {STEPS.map((s) => (
          <div
            key={s.num}
            className="p-4 rounded-lg border"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <div className="flex items-center gap-3 mb-2">
              <StepBadge n={s.num} />
              <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                Step {s.num} — {s.label}
              </h3>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {s.body}
            </p>
          </div>
        ))}
      </div>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
