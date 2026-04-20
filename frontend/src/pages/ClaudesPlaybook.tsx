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
      className="text-xs uppercase tracking-wider font-semibold mt-8 mb-3"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </h2>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
    >
      {children}
    </div>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
      {children}
    </h3>
  )
}

function CardBody({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
      {children}
    </p>
  )
}

function Callout({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="mt-3 p-3 rounded text-sm leading-relaxed"
      style={{
        backgroundColor: 'color-mix(in srgb, var(--accent) 8%, transparent)',
        color: 'var(--text-secondary)',
        borderLeft: '3px solid var(--accent)',
      }}
    >
      {children}
    </div>
  )
}

type HorsemenCard = {
  number: string
  title: string
  body: string
}

const FOUR_HORSEMEN: HorsemenCard[] = [
  {
    number: '1',
    title: 'Probability',
    body: 'Does the delta target give a statistical edge? A delta of -0.20 to -0.30 on a short put means roughly 70-80% probability of profit. Claude confirms the delta target is met before recommending entry.',
  },
  {
    number: '2',
    title: 'Volatility',
    body: 'Are options priced richly enough to sell? For credit strategies, IV Rank must be ≥ 30 — meaning options are in the upper third of their 52-week price range. For debit spreads, IVR < 30 means we\'re buying options at a relative discount.',
  },
  {
    number: '3',
    title: 'Time Decay',
    body: 'Is theta working in our favor? DTE of 21-35 days puts us in the theta acceleration zone — the sweet spot where time decay speeds up enough to generate consistent income without excess gamma risk.',
  },
  {
    number: '4',
    title: 'Market Direction',
    body: 'Does the current market regime support this strategy type? Bull regime favors put spreads and the wheel. Bear regime favors call spreads. Neutral regime is needed for iron condors. Claude never fights the regime.',
  },
]

type GreekRow = {
  greek: string
  what: string
  why: string
}

const GREEKS: GreekRow[] = [
  {
    greek: 'Delta',
    what: '-0.20 to -0.30 for puts, 0.20 to 0.35 for calls',
    why: 'Defines probability of profit. Lower delta means higher win rate but less premium collected per trade.',
  },
  {
    greek: 'Theta',
    what: 'Positive for short options — we collect decay daily',
    why: 'This is how the bot makes money. Theta accelerates between 21-35 DTE, which is why that range is the entry sweet spot.',
  },
  {
    greek: 'Vega',
    what: 'We are short vega on all credit strategies',
    why: 'If IV drops after we sell, the option loses value and we profit. This is why we only sell when IV is already elevated.',
  },
  {
    greek: 'Gamma',
    what: 'Danger zone below 7 DTE',
    why: 'Gamma makes delta unstable near expiration — a small move in the stock can cause a big move in the option price. The bot avoids holding into the last week to avoid this.',
  },
]

type HeuristicItem = {
  title: string
  body: string
}

const HEURISTICS: HeuristicItem[] = [
  {
    title: 'Credit-to-Width Awareness',
    body: 'For credit spreads, experienced practitioners target 30-40% credit-to-width ratio. The bot\'s hard minimum is 15%, but Claude is instructed to flag trades in the 15-25% range as "below preferred range" and require additional confirming signals before recommending entry. A ratio above 25% is acceptable; above 35% is an excellent setup.',
  },
  {
    title: 'Debit-to-Width Ratio',
    body: 'For the long call vertical (debit spread), Claude won\'t recommend paying more than 40% of the spread width. Paying more means risking over 60% of the spread width to gain less than 40% — unfavorable math even with high probability. Ideal entry is 25-35% of width.',
  },
  {
    title: 'Support/Resistance Strike Placement',
    body: 'Claude prefers placing short strikes outside major support/resistance levels, not at them. For puts: below the 50-day SMA or recent 20-day low. For calls: above the recent 20-day high. This means the stock has to break through support or resistance and keep moving before it threatens the short strike.',
  },
  {
    title: 'Spread Width Guidelines',
    body: 'Index ETFs (SPY, QQQ, IWM): $10 wide spreads. Large-cap stocks ($100+): roughly 10% of stock price. Mid-cap stocks ($30-$100): $5 wide. These are guidelines, not hard rules — the guardrails enforce max loss per trade regardless of spread width.',
  },
  {
    title: 'Counterfactual Check',
    body: 'For open positions, Claude is instructed to ask: "If this position were not already open, would I recommend opening it right now?" If the answer is no — regime shifted, IV collapsed, the stock has deteriorated — Claude recommends closing regardless of current P&L. This prevents holding positions out of anchoring bias rather than sound judgment.',
  },
]

type PrincipleItem = {
  title: string
  body: string
}

const PRINCIPLES: PrincipleItem[] = [
  {
    title: 'Capital preservation first.',
    body: 'A steady 15-20% annualized return with low drawdowns compounds far better than volatile swings. Claude targets consistency over home runs. It never sells closer-to-the-money strikes just to collect more premium.',
  },
  {
    title: 'Never chase premium.',
    body: 'If no contract meets all criteria simultaneously, the answer is SKIP. There is always another cycle. Forcing a trade that barely qualifies is how small losses turn into large ones.',
  },
  {
    title: 'The Black Swan lesson.',
    body: 'You can be right about every factor you analyze and still lose on something you never considered — overnight news, geopolitical shocks, surprise events. This is why every defensive layer exists: defined-risk only, 200% stop loss on credit spreads, 10% per-position cap, circuit breaker. Claude is explicitly instructed never to loosen the 200% stop loss to "let a trade work out."',
  },
  {
    title: 'Realistic expectations.',
    body: 'A well-managed wheel and spread portfolio should target 15-30% annualized returns. Claude does not chase higher returns by overconcentrating positions, ignoring skip signals, or holding losing positions hoping for recovery.',
  },
]

export function ClaudesPlaybook() {
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <div ref={contentRef} className="max-w-5xl">
      <PageHeader
        title="Claude's Playbook"
        subtitle="The knowledge, principles, and heuristics Claude carries on every trading decision — regardless of strategy."
      />

      {/* Section 1: What Is the System Prompt */}
      <SectionHeader>What Is the System Prompt?</SectionHeader>
      <Card>
        <CardBody>
          Every time the bot asks Claude for a trading decision, it sends two things: a{' '}
          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>system prompt</span> — stable
          knowledge that doesn't change between calls — and a{' '}
          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>user message</span> — live market
          data and strategy-specific instructions that change every call.
        </CardBody>
        <CardBody>
          <span style={{ color: 'var(--text-primary)', fontStyle: 'italic' }}>
            The system prompt is Claude's operating manual.
          </span>{' '}
          It defines how Claude thinks about trades, what it prioritizes, and what lines it won't cross.
          This page explains what's in that manual. The live data and strategy-specific rules are covered
          on the Data Sources and Strategies pages.
        </CardBody>
      </Card>

      {/* Section 2: Claude's Role */}
      <SectionHeader>Claude's Role</SectionHeader>
      <Card>
        <CardTitle>A disciplined, patient trader — not an optimizer.</CardTitle>
        <CardBody>
          Claude is instructed to act as a disciplined options trader who prioritizes capital preservation
          over aggressive premium collection. It never forces trades that don't meet criteria, even when
          the market looks tempting. When confidence is low, Claude defaults to SKIP — doing nothing is
          always a valid choice.
        </CardBody>
      </Card>

      {/* Section 3: Four Horsemen */}
      <SectionHeader>The Four Horsemen — Trade Quality Filter</SectionHeader>
      <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
        Before recommending any trade, Claude confirms all four factors are favorable. Amateurs focus only
        on direction. Professionals weight all four equally.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {FOUR_HORSEMEN.map((h) => (
          <div
            key={h.number}
            className="p-4 rounded-lg border h-full"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <div className="flex items-center gap-2 mb-2">
              <span
                className="text-xs font-mono w-5 h-5 rounded-full flex items-center justify-center font-bold"
                style={{
                  backgroundColor: 'color-mix(in srgb, var(--accent) 18%, transparent)',
                  color: 'var(--accent)',
                }}
              >
                {h.number}
              </span>
              <h3 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                {h.title}
              </h3>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {h.body}
            </p>
          </div>
        ))}
      </div>
      <Callout>
        If any single Horseman is clearly unfavorable, Claude skips the trade — even if the other three
        look great. This prevents trades where one hidden weakness undermines an otherwise attractive setup.
      </Callout>

      {/* Section 4: Greeks Awareness */}
      <SectionHeader>Greeks Awareness</SectionHeader>
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
              <th className="px-4 py-3 font-medium">Greek</th>
              <th className="px-4 py-3 font-medium">What Claude Looks For</th>
              <th className="px-4 py-3 font-medium">Why It Matters</th>
            </tr>
          </thead>
          <tbody>
            {GREEKS.map((row, i) => (
              <tr
                key={row.greek}
                style={{
                  borderTop: i === 0 ? '1px solid var(--border)' : '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                <td className="px-4 py-3 font-semibold" style={{ color: 'var(--text-primary)' }}>
                  {row.greek}
                </td>
                <td className="px-4 py-3">{row.what}</td>
                <td className="px-4 py-3">{row.why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Callout>
        Claude checks IV Rank — where current implied volatility sits within its 52-week range — to
        determine whether options are cheap or expensive. IVR ≥ 30 means premiums are worth selling.
        Below 30, they're too thin.
      </Callout>

      {/* Section 5: Expert Heuristics */}
      <SectionHeader>Expert Heuristics</SectionHeader>
      <p className="text-sm mb-4" style={{ color: 'var(--text-secondary)' }}>
        Experience-based rules synthesized from professional options trading practice. These shape how
        Claude evaluates trades beyond the mechanical entry criteria.
      </p>
      <div className="flex flex-col gap-3">
        {HEURISTICS.map((h) => (
          <Card key={h.title}>
            <CardTitle>{h.title}</CardTitle>
            <CardBody>{h.body}</CardBody>
          </Card>
        ))}
      </div>

      {/* Section 6: Risk Philosophy */}
      <SectionHeader>Risk Philosophy</SectionHeader>
      <div className="grid gap-3 md:grid-cols-2">
        {PRINCIPLES.map((p) => (
          <div
            key={p.title}
            className="p-4 rounded-lg border h-full"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
              {p.title}
            </h3>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {p.body}
            </p>
          </div>
        ))}
      </div>

      {/* Section 6b: Macro Calendar */}
      <SectionHeader>Macro Calendar</SectionHeader>
      <Card>
        <CardTitle>Tier 1 macro events modify entry, management, and closure behavior.</CardTitle>
        <CardBody>
          Every context package includes a{' '}
          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>next_macro_event</span> field
          showing the nearest upcoming FOMC rate decision, CPI release, or Non-Farm Payrolls — with
          hours-until and trading-day-proximity flags. New entries are blocked the day of and the trading
          day before any Tier 1 event. This block fires before Claude is called, so Claude never sees
          candidates on restricted days. Management of existing positions is never blocked.
        </CardBody>
        <Callout>
          For positions opened within 3 trading days of an upcoming Tier 1 event, Claude takes profit at
          30% of initial credit rather than the usual 50%, prefers rolling the position further from price,
          and treats a Tier 1 event falling inside the position's DTE window as a strong argument for early
          closure. Tier 1 events historically drive 1–2% single-session moves — a position structured
          without pricing in an imminent event is carrying mispriced risk.
        </Callout>
      </Card>
      <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
        In progress — scoped but not yet shipped. Kill switch:{' '}
        <code className="text-xs font-mono">MACRO_EVENT_BLOCK_ENABLED</code> (default{' '}
        <code className="text-xs font-mono">true</code>).
      </p>

      {/* Section 6c: Cross-Account Anti-Crowding */}
      <SectionHeader>Cross-Account Anti-Crowding</SectionHeader>
      <Card>
        <CardTitle>
          New entries that would stack the same directional risk on the same underlying are blocked before
          Claude is called.
        </CardTitle>
        <CardBody>
          Every context package includes a{' '}
          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>book_exposure</span> field
          showing positions across all accounts, grouped by underlying and directional-risk family. A wheel
          cash-secured put on AAPL and a bull put spread on AAPL are the same directional bet — only one
          runs at a time. The block fires at the candidate pre-check stage, before Claude sees the
          candidate at all.
        </CardBody>
        <CardBody>
          One exception: Standard Wheel and Turnover Wheel are explicitly allowed to co-exist on the same
          underlying. This preserves the comparative experiment between the two variants — blocking one
          would silently invalidate the test. An open iron condor occupies both short-put and short-call
          families simultaneously on its underlying, so it blocks both a new wheel put and a new bear call
          spread on the same name.
        </CardBody>
        <Callout>
          Without cross-account visibility, each per-account Claude call had zero knowledge of what the
          other accounts were carrying. The bot could silently double or triple correlated short-put
          exposure across accounts with no diversification benefit. Cross-account anti-crowding closes this
          gap — it is the largest single decision-quality improvement in the current architecture.
        </Callout>
      </Card>
      <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
        Planned — scoped, not yet started. Kill switch:{' '}
        <code className="text-xs font-mono">CROSS_ACCOUNT_ANTI_CROWDING_ENABLED</code> (default{' '}
        <code className="text-xs font-mono">true</code>).
      </p>

      {/* Section 6d: Historical Win-Rate Scoring */}
      <SectionHeader>Historical Win-Rate Scoring</SectionHeader>
      <Card>
        <CardTitle>
          Candidates are ranked by historical win rate at the (symbol, strategy) level — not just by
          liquidity.
        </CardTitle>
        <CardBody>
          Research metadata for each candidate includes a{' '}
          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>win_rate</span> field drawn from
          a weekly ORATS backtest sweep over the most recent 6 months. A bounded multiplier (0.8× to 1.2×,
          no hard exclusion) combines with the existing Phase 1 liquidity multiplier at the candidate
          pre-check stage. Higher historical performers rank up; lower performers rank down. When the
          sample size for a (symbol, strategy) pair is below the threshold, the multiplier returns neutral.
        </CardBody>
        <CardBody>
          Claude sees this as ranked context — something like:{' '}
          <em style={{ color: 'var(--text-primary)' }}>
            "Tier A liquidity symbol with 64% historical win rate at our parameters"
          </em>{' '}
          versus{' '}
          <em style={{ color: 'var(--text-primary)' }}>
            "Tier A liquidity but only 42% win rate historically — weaker than it looks."
          </em>
        </CardBody>
        <Callout>
          Backtests assume mid-price fills. Treat win rates as relative rankings between symbols and
          strategies, not as absolute expected win rates. This is the only roadmap feature that adds a
          genuinely new high-quality signal to Claude's input — every other improvement either removes bad
          inputs or measures what is already happening.
        </Callout>
      </Card>
      <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
        Planned — Phase 2 of the research layer; Phase 1 liquidity scoring is already live. Kill switch:{' '}
        <code className="text-xs font-mono">RESEARCH_WINRATE_MULTIPLIER_ENABLED</code> (default{' '}
        <code className="text-xs font-mono">false</code> initially — log-only before enforcing).
      </p>

      {/* Section 7: What Claude Does NOT Know */}
      <SectionHeader>What Claude Does NOT Know</SectionHeader>
      <Card>
        <ul className="flex flex-col gap-3">
          {[
            'Claude has no memory between API calls. It doesn\'t remember what it decided last cycle — each decision is made fresh from the current snapshot.',
            "Claude doesn't see your total P&L or account equity history. It sees the current portfolio snapshot provided in the user message.",
            "Claude can't predict earnings surprises, geopolitical events, or overnight news. The guardrails and circuit breaker exist precisely because Claude's analysis can be correct and still lose to the unknowable.",
            "Claude's recommendations are validated by Python guardrails before execution. If Claude recommends something that violates a hard rule, the trade is rejected automatically — Claude doesn't get the final word.",
          ].map((item) => (
            <li key={item} className="flex gap-3 items-start">
              <span
                className="mt-0.5 shrink-0 text-xs font-mono"
                style={{ color: 'var(--text-muted)' }}
              >
                —
              </span>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {item}
              </p>
            </li>
          ))}
        </ul>
      </Card>

      {/* Section 8: How This Knowledge Gets Updated */}
      <SectionHeader>How This Knowledge Gets Updated</SectionHeader>
      <div className="flex flex-col gap-3">
        <Card>
          <CardTitle>The system prompt is a file in the codebase.</CardTitle>
          <CardBody>
            When we learn new heuristics from books, courses, or performance analysis, we synthesize them
            and add them to <code className="text-xs font-mono">prompts/system.md</code>. Changes take
            effect on the next scheduled bot cycle — no restart needed. Claude reads the prompt fresh on
            every API call.
          </CardBody>
        </Card>
        <Card>
          <CardTitle>The prompt is cached for up to 1 hour.</CardTitle>
          <CardBody>
            Anthropic's servers cache the system prompt across calls within the same hour. This means we
            can make it as detailed as needed without proportionally increasing cost — subsequent calls
            within the hour reuse the cached version at a 90% discount on those tokens.
          </CardBody>
        </Card>
      </div>

      <p className="text-xs mt-8" style={{ color: 'var(--text-muted)' }}>
        This bot runs on paper trading accounts. No real capital is at risk.
      </p>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
