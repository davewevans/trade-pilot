import { useRef } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

type Reason = {
  reason: string
  body: string
  applies: string
}

type Category = {
  title: string
  rows: Reason[]
}

const CATEGORIES: Category[] = [
  {
    title: 'Market Conditions (pre-Claude checks)',
    rows: [
      {
        reason: 'IV rank too low',
        body:
          'Options premiums are cheap right now — not worth selling. The bot waits for higher IV before opening credit positions. Threshold: IV rank < 30 for wheel/spreads, < 40 for iron condor.',
        applies: 'Wheel, Bull Put, Bear Call, Iron Condor',
      },
      {
        reason: 'IV environment wrong',
        body:
          "The overall IV environment (LOW/MODERATE/HIGH) doesn't match what this strategy requires. Long Call Vertical needs LOW IV; Iron Condor needs HIGH IV.",
        applies: 'Iron Condor, Long Call Vertical',
      },
      {
        reason: 'VIX out of range',
        body:
          'The VIX is either too low (premiums too thin) or too high (too risky). Iron condor specifically requires VIX between 18–35.',
        applies: 'Iron Condor',
      },
      {
        reason: 'Market regime mismatch',
        body:
          'The bot classified the current market as BULL, BEAR, NEUTRAL, CRASH, or EUPHORIA. This strategy is not appropriate for the current regime. Bear Call Spread needs BEAR; Long Call Vertical needs BULL.',
        applies: 'All spread strategies',
      },
      {
        reason: 'CRASH regime',
        body:
          'VIX ≥ 35. The bot opens no new positions of any kind during a crash. Existing positions are still managed.',
        applies: 'All strategies',
      },
      {
        reason: 'Fear & Greed extreme',
        body:
          'The Fear & Greed index is below 20 (extreme fear). High assignment risk — the bot pauses new CSPs.',
        applies: 'Wheel',
      },
      {
        reason: 'VIX extreme',
        body:
          'VIX > 35. Even outside CRASH classification, extreme VIX triggers a pause on new wheel entries.',
        applies: 'Wheel',
      },
      {
        reason: 'IV overvalued check (ORATS forecast)',
        body:
          'ORATS forecasts IV to be lower than current levels, meaning options are overpriced. For credit strategies this is favorable, but for the Long Call Vertical (a debit strategy), overvalued IV means overpaying — the bot skips.',
        applies: 'Long Call Vertical',
      },
      {
        reason: 'IV undervalued check (ORATS forecast)',
        body:
          'ORATS forecasts IV to be higher than current levels, meaning options are underpriced. Selling underpriced options gives poor edge — the bot skips credit entries.',
        applies: 'Bear Call, Iron Condor, Short Strangle',
      },
      {
        reason: 'Term structure backwardation',
        body:
          'Short-term IV exceeds long-term IV (backwardation). This signals near-term fear and undermines neutral strategy theses. Iron condors, strangles, and calendar spreads are blocked.',
        applies: 'Iron Condor, Short Strangle, Calendar Spread',
      },
      {
        reason: 'Contango required',
        body:
          'Calendar spreads require positive contango (short-term IV < long-term IV) for the time-decay differential to work in your favor. Without contango, the structural edge disappears.',
        applies: 'Calendar Spread',
      },
      {
        reason: 'Spread yield too low',
        body:
          'The credit received relative to the stock price is below 0.1%. This normalizes credit quality across different stock prices — a $0.50 credit on a $500 stock is too thin even if it exceeds old fixed thresholds.',
        applies: 'Bull Put, Bear Call, Short Strangle',
      },
    ],
  },
  {
    title: 'Earnings Risk',
    rows: [
      {
        reason: 'Earnings too close (Wheel CSP)',
        body:
          "A company's earnings report is within 14 days. Earnings cause unpredictable price swings that can wipe out a short put's entire premium in one move.",
        applies: 'Wheel CSP',
      },
      {
        reason: 'Earnings too close (Wheel CC / Spreads)',
        body: "Earnings are within 21 days of the trade's expiration.",
        applies: 'Wheel CC, Bull Put, Bear Call',
      },
      {
        reason: 'Earnings too close (Iron Condor)',
        body:
          'Earnings are within 30 days. Iron condors need extra buffer because both sides of the trade are at risk from a large move.',
        applies: 'Iron Condor',
      },
      {
        reason: 'Earnings within DTE window',
        body:
          'For Long Call Vertical, earnings fall within the expiration window of the trade — any earnings event during the hold period invalidates the strategy.',
        applies: 'Long Call Vertical',
      },
      {
        reason: 'Ex-dividend date risk',
        body:
          "A stock's ex-dividend date falls within the trade's DTE window. Short calls near the ex-div date carry early assignment risk — the bot avoids this.",
        applies: 'Bear Call Spread',
      },
    ],
  },
  {
    title: 'Short Strangle & Calendar Spread Specific',
    rows: [
      {
        reason: 'Delta breach risk (strangle)',
        body:
          'Either the short put or short call delta has exceeded 0.40, meaning the position is being tested and the stock has moved significantly toward one of the strikes. Without protective wings, the bot closes immediately.',
        applies: 'Short Strangle',
      },
      {
        reason: 'Earnings between expirations (calendar)',
        body:
          'For calendar spreads, an earnings event falls between the short and long expiration dates. A post-earnings IV crush would collapse the long leg\'s value while the short leg may have already expired — destroying the spread\'s edge.',
        applies: 'Calendar Spread',
      },
      {
        reason: 'Max rolls reached (calendar)',
        body:
          'The short leg of the calendar has already been rolled twice. Further rolling increases complexity and capital commitment — after two rolls the bot closes the entire position rather than continuing to extend.',
        applies: 'Calendar Spread',
      },
      {
        reason: 'ATR breach (calendar)',
        body:
          'The stock has moved more than 1 ATR (Average True Range) from the calendar\'s strike price. Calendar spreads have a narrow profit zone — once the stock moves this far, the directional thesis is broken and the position is closed.',
        applies: 'Calendar Spread',
      },
    ],
  },
  {
    title: 'Position & Capital Rules',
    rows: [
      {
        reason: 'Already have open position on this symbol',
        body:
          'The bot never opens two of the same strategy on the same stock at the same time. One CSP per symbol, one condor per symbol, etc.',
        applies: 'All strategies',
      },
      {
        reason: 'Position too large',
        body:
          "The trade's maximum possible loss exceeds the allowed percentage of buying power. Each strategy has its own limit (10% for wheel CSP, 5% for iron condor, 2% for most spreads, 1% for long call vertical).",
        applies: 'All strategies',
      },
      {
        reason: 'Shared account over-allocated',
        body:
          'The combined maximum loss of all open positions on the default account (Bull Put + Bear Call + Long Call Vertical) would exceed 10% of buying power if this trade were added.',
        applies: 'Bull Put, Bear Call, Long Call Vertical',
      },
      {
        reason: 'Insufficient buying power',
        body: 'Not enough cash in the account to support the position.',
        applies: 'Wheel CSP',
      },
    ],
  },
  {
    title: 'Contract Quality',
    rows: [
      {
        reason: 'No candidates found',
        body:
          "The bot scanned the options chain but couldn't find a contract that meets all the delta, DTE, and liquidity requirements simultaneously.",
        applies: 'All strategies',
      },
      {
        reason: 'DTE out of range',
        body:
          'The best available contract expires either too soon or too far out. Each strategy has its own DTE window.',
        applies: 'All strategies',
      },
      {
        reason: 'Credit too low',
        body:
          'The premium collected is below the minimum threshold — not worth the risk and commission for such a small credit.',
        applies: 'Wheel, Iron Condor, Bull Put, Bear Call',
      },
      {
        reason: 'Debit too high or too low',
        body:
          'For the Long Call Vertical (debit strategy), the cost to enter falls outside acceptable bounds.',
        applies: 'Long Call Vertical',
      },
      {
        reason: 'Invalid option symbol',
        body:
          'The OCC-format symbol fails validation. This is a data quality check — a malformed symbol would cause an order rejection at the broker.',
        applies: 'All strategies',
      },
    ],
  },
  {
    title: 'Circuit Breaker',
    rows: [
      {
        reason: 'Circuit breaker YELLOW',
        body:
          'Daily losses have reached the "reduce" threshold. No new positions are opened — only existing positions are managed.',
        applies: 'All strategies',
      },
      {
        reason: 'Circuit breaker RED',
        body:
          'A halt threshold has been breached (daily, weekly, or drawdown). All new position entry is stopped.',
        applies: 'All strategies',
      },
      {
        reason: 'Bot halted (lock file)',
        body:
          'The portfolio dropped 15% from its peak. The bot writes a lock file and stops all trading until a human manually deletes it.',
        applies: 'All strategies',
      },
    ],
  },
]

function CategoryBlock({ cat }: { cat: Category }) {
  return (
    <section className="mb-8">
      <h2
        className="text-xs uppercase tracking-wider font-semibold mb-3"
        style={{ color: 'var(--text-muted)' }}
      >
        {cat.title}
      </h2>

      {/* Desktop table */}
      <div
        className="hidden md:block rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr style={{ color: 'var(--text-muted)' }} className="text-left text-xs uppercase tracking-wider">
              <th className="px-4 py-3 font-medium w-1/4">Reason</th>
              <th className="px-4 py-3 font-medium">Plain-English Explanation</th>
              <th className="px-4 py-3 font-medium w-1/5">Applies To</th>
            </tr>
          </thead>
          <tbody>
            {cat.rows.map((r) => (
              <tr key={r.reason} style={{ borderTop: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                <td className="px-4 py-3 align-top font-medium" style={{ color: 'var(--text-primary)' }}>
                  {r.reason}
                </td>
                <td className="px-4 py-3 align-top">{r.body}</td>
                <td className="px-4 py-3 align-top text-xs">{r.applies}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile cards */}
      <div className="md:hidden space-y-2">
        {cat.rows.map((r) => (
          <div
            key={r.reason}
            className="p-3 rounded-lg border"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
              {r.reason}
            </div>
            <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>{r.body}</p>
            <div className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
              <span className="uppercase tracking-wider">Applies to:</span> {r.applies}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

export function SkipReasons() {
  const contentRef = useRef<HTMLDivElement>(null)
  return (
    <div ref={contentRef} className="max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          Why Trades Get Skipped
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>
          When the bot skips a trade, it always logs the exact reason. Here's what each reason means.
        </p>
      </div>

      {CATEGORIES.map((c) => (
        <CategoryBlock key={c.title} cat={c} />
      ))}

      <section className="mb-8">
        <h2
          className="text-xs uppercase tracking-wider font-semibold mb-3"
          style={{ color: 'var(--text-muted)' }}
        >
          Claude's Own Judgment
        </h2>
        <div
          className="p-4 rounded-lg border"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 6%, var(--bg-card))',
            borderColor: 'var(--border)',
            borderLeft: '3px solid var(--accent)',
          }}
        >
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            Even when all pre-conditions pass and guardrails don't block the trade, Claude can still
            choose to SKIP based on its assessment of the full context — for example, conflicting
            technical signals, a stock it considers too risky given current macro conditions, or a
            premium it considers insufficient for the risk. These skips are logged with Claude's
            reasoning, which you can read in the Decisions log.
          </p>
        </div>
      </section>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
