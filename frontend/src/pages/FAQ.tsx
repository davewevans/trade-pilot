import { useRef, useState } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

type QA = { q: string; a: string }
type Group = { title: string; items: QA[] }

const GROUPS: Group[] = [
  {
    title: 'What the Bot Is Doing',
    items: [
      {
        q: "Why didn't the bot trade today?",
        a: "Several things can prevent a trade: IV rank was too low (premiums too cheap to sell), the market regime blocked the strategy, earnings were too close, no contract met all the entry criteria, or the circuit breaker is active. The Decisions tab logs the exact skip reason for every cycle. Check there first.",
      },
      {
        q: 'How often does the bot run?',
        a: 'The scheduler runs four jobs each trading day: pre-market (6:00 AM ET) for data validation and setup, market open (9:30 AM ET) for new position entry evaluation, and position checks at 10:00 AM, 12:00 PM, and 2:00 PM ET to manage open positions.',
      },
      {
        q: 'What does "SKIP" mean in the decisions log?',
        a: "The bot evaluated the current market conditions and decided not to open a new position. Every skip includes a reason. Skipping is not a failure — it's the bot doing its job correctly when conditions don't meet the entry criteria.",
      },
      {
        q: 'Why does the bot sometimes skip even when the market looks fine?',
        a: "The bot applies multiple filters simultaneously: IV rank, earnings proximity, market regime, delta range, DTE window, position sizing limits, and more. A stock can look fine visually while still failing one specific filter. The skip reason in the Decisions tab will tell you which condition wasn't met.",
      },
      {
        q: 'Is the bot trading real money?',
        a: 'Currently running on Alpaca paper trading accounts — simulated trades with fake money. No real capital is at risk. The DRY_RUN setting can also be enabled to run the full decision cycle without placing even paper orders.',
      },
    ],
  },
  {
    title: 'Understanding the Dashboard',
    items: [
      {
        q: 'What is the circuit breaker status?',
        a: 'A green/yellow/red indicator showing the current portfolio-level risk status. GREEN means normal operation. YELLOW means the portfolio is down ≥ 1.5% today and no new positions are being opened. RED means a larger loss threshold was breached and all new entries are halted. See the Circuit Breakers page for full details.',
      },
      {
        q: 'What does the market regime mean?',
        a: "The bot's classification of current market conditions — BULL, NEUTRAL, BEAR, CRASH, or EUPHORIA — based on VIX, SPX trend, and Fear & Greed. This determines which strategies are eligible to trade each cycle. See the Market Regimes page for how each regime is classified.",
      },
      {
        q: 'What is IV rank and why does it matter?',
        a: 'IV rank (IVR) measures where current implied volatility sits within its 52-week range on a 0–100 scale. The bot sells options when IV is elevated (IVR ≥ 30) because higher IV means richer premiums for the same amount of risk. When IV is low, premiums are too thin to justify the trade.',
      },
      {
        q: 'What does "confirmed regime" mean vs the current reading?',
        a: 'The raw regime is what the signals show right now. The confirmed regime is what the bot actually acts on — it requires 3 consecutive identical readings before changing. This prevents the bot from overreacting to a single volatile day. If they differ, the bot is waiting to see if the new regime is sustained.',
      },
      {
        q: 'Why are there three separate accounts?',
        a: "Each account runs a distinct strategy type. The Wheel account trades cash-secured puts and covered calls. The Iron Condor account runs iron condors. The Spreads account adaptively runs bull put spreads, bear call spreads, or long call verticals depending on market regime and IV environment. Separating accounts keeps capital allocation clean and prevents one strategy's losses from affecting another's buying power.",
      },
    ],
  },
  {
    title: 'How Claude Is Involved',
    items: [
      {
        q: 'What does Claude actually decide?',
        a: 'Given the full market context — price, technicals, IV rank, macro data, earnings dates, account state — Claude decides whether to open a trade, hold, or skip. If it opens a trade, it specifies the exact contract: symbol, strike, expiration, and limit price. Claude also decides when to roll or close existing positions.',
      },
      {
        q: 'Can Claude override the guardrails?',
        a: "No. The guardrails are enforced in Python code after Claude's response is received. If Claude recommends a trade that violates a hard rule — earnings too close, position too large, invalid symbol, wrong order type — the trade is rejected before it reaches the broker. Claude's reasoning is logged but the order is not placed.",
      },
      {
        q: 'Does Claude see the same data every time?',
        a: 'No — the context package is rebuilt fresh every cycle from live data. Claude sees the current price, current IV rank, current macro conditions, and the current state of all open positions. It does not have memory of past decisions from prior cycles.',
      },
      {
        q: 'What happens if Claude returns bad JSON?',
        a: "The bot validates Claude's response format before doing anything with it. If the response is malformed, missing required fields, or contains an unrecognized action, the cycle logs an error and skips — no trade is placed.",
      },
    ],
  },
  {
    title: 'Risk and Operations',
    items: [
      {
        q: 'What happens if the bot loses a lot of money?',
        a: 'The circuit breaker system has progressive responses: at 1.5% daily loss it reduces position sizing, at 3% it halts new entries for the day, at 5% weekly loss it halts for the week, at 10% drawdown from peak it halts indefinitely, and at 15% drawdown it writes a lock file that requires manual deletion to resume. No single bad day can wipe the account because sizing limits and halt thresholds kick in first.',
      },
      {
        q: 'What is assignment and should I be worried about it?',
        a: 'Assignment happens when a short put expires in the money — the bot is obligated to buy 100 shares at the strike price. For the wheel strategy, this is an expected and acceptable outcome, not a failure. The bot transitions to LONG_STOCK state and starts looking for a covered call to sell. The CSP was originally sold with cash reserved specifically to buy those shares.',
      },
      {
        q: 'What is a roll and why does the bot do it?',
        a: "Rolling means closing the current option and opening a new one with a better strike, later expiration, or both — usually for a net credit. The bot rolls when a position moves against it (delta doubles from entry) or when expiration is close and the position hasn't resolved. Rolling extends the time for the trade to work without taking a loss.",
      },
      {
        q: 'What happens if the API goes down or the bot crashes?',
        a: "Open positions remain open at the broker — they don't close just because the bot stopped running. The bot resumes from its last saved state when restarted. The circuit breaker state, regime history, and spread tracker state are all persisted to disk and survive restarts.",
      },
    ],
  },
]

function QAItem({
  qa,
  open,
  onToggle,
}: {
  qa: QA
  open: boolean
  onToggle: () => void
}) {
  return (
    <div
      className="rounded-lg border overflow-hidden"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-4 py-3 flex items-start gap-3 transition-colors"
        style={{ color: 'var(--text-primary)' }}
        aria-expanded={open}
      >
        <span
          className="mt-0.5 text-xs shrink-0 font-mono"
          style={{ color: 'var(--accent)' }}
          aria-hidden="true"
        >
          {open ? '▾' : '▸'}
        </span>
        <span className="font-medium text-sm">{qa.q}</span>
      </button>
      {open && (
        <div
          className="px-4 pb-4 pl-10 text-sm leading-relaxed"
          style={{ color: 'var(--text-secondary)' }}
        >
          {qa.a}
        </div>
      )}
    </div>
  )
}

export function FAQ() {
  // Track open items by a "groupIdx:itemIdx" key — multiple can be open at once.
  const [openKeys, setOpenKeys] = useState<Set<string>>(new Set())
  const contentRef = useRef<HTMLDivElement>(null)

  const toggle = (key: string) =>
    setOpenKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const expandAll = () => {
    const all = new Set<string>()
    GROUPS.forEach((g, gi) => g.items.forEach((_, ii) => all.add(`${gi}:${ii}`)))
    setOpenKeys(all)
  }
  const collapseAll = () => setOpenKeys(new Set())

  return (
    <div ref={contentRef} className="max-w-5xl">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
            FAQ
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
            Plain-English answers to the questions that come up most.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={expandAll}
            className="text-xs px-3 py-1.5 rounded border transition-colors"
            style={{
              backgroundColor: 'var(--bg-card)',
              borderColor: 'var(--border)',
              color: 'var(--text-secondary)',
            }}
          >
            Expand all
          </button>
          <button
            type="button"
            onClick={collapseAll}
            className="text-xs px-3 py-1.5 rounded border transition-colors"
            style={{
              backgroundColor: 'var(--bg-card)',
              borderColor: 'var(--border)',
              color: 'var(--text-secondary)',
            }}
          >
            Collapse all
          </button>
        </div>
      </div>

      {GROUPS.map((group, gi) => (
        <section key={group.title} className="mb-8">
          <h2
            className="text-xs uppercase tracking-wider font-semibold mb-3"
            style={{ color: 'var(--text-muted)' }}
          >
            {group.title}
          </h2>
          <div className="space-y-2">
            {group.items.map((qa, ii) => {
              const key = `${gi}:${ii}`
              return (
                <QAItem
                  key={key}
                  qa={qa}
                  open={openKeys.has(key)}
                  onToggle={() => toggle(key)}
                />
              )
            })}
          </div>
        </section>
      ))}

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
