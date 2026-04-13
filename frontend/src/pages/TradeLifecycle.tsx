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
      className="text-lg font-semibold mt-10 mb-3"
      style={{ color: 'var(--text-primary)' }}
    >
      {children}
    </h2>
  )
}

function Card({
  children,
  accent,
  topAccent,
}: {
  children: React.ReactNode
  accent?: string
  topAccent?: string
}) {
  return (
    <div
      className="p-5 rounded-lg border"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderLeft: accent ? `4px solid ${accent}` : undefined,
        borderTop: topAccent ? `3px solid ${topAccent}` : undefined,
      }}
    >
      {children}
    </div>
  )
}

function Callout({
  children,
  accent = 'var(--accent)',
}: {
  children: React.ReactNode
  accent?: string
}) {
  return (
    <div
      className="mt-4 p-4 rounded text-sm leading-relaxed"
      style={{
        backgroundColor: `color-mix(in srgb, ${accent} 8%, transparent)`,
        color: 'var(--text-secondary)',
        borderLeft: `3px solid ${accent}`,
      }}
    >
      {children}
    </div>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-sm leading-relaxed mb-3"
      style={{ color: 'var(--text-secondary)' }}
    >
      {children}
    </p>
  )
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3
      className="font-semibold text-sm mb-2"
      style={{ color: 'var(--text-primary)' }}
    >
      {children}
    </h3>
  )
}

function TimeRow({ time, children }: { time: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 py-3" style={{ borderTop: '1px solid var(--border)' }}>
      <span
        className="text-xs px-2 py-1 rounded font-mono shrink-0 h-fit"
        style={{
          backgroundColor: 'color-mix(in srgb, var(--accent) 14%, transparent)',
          color: 'var(--text-primary)',
          border: '1px solid color-mix(in srgb, var(--accent) 30%, transparent)',
        }}
      >
        {time}
      </span>
      <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </p>
    </div>
  )
}

function Pill({ children, color = 'var(--accent)' }: { children: React.ReactNode; color?: string }) {
  return (
    <span
      className="text-xs px-3 py-1.5 rounded-full font-mono"
      style={{
        backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)`,
        color: 'var(--text-primary)',
        border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
      }}
    >
      {children}
    </span>
  )
}

function Arrow() {
  return <span style={{ color: 'var(--text-muted)' }}>→</span>
}

function StepCard({
  n,
  date,
  title,
  children,
  pnl,
  accent = 'var(--accent)',
}: {
  n: number
  date: string
  title: string
  children: React.ReactNode
  pnl: string
  accent?: string
}) {
  return (
    <div
      className="p-5 rounded-lg border"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderLeft: `4px solid ${accent}`,
      }}
    >
      <div className="flex items-baseline gap-3 mb-2">
        <span
          className="text-xs font-mono px-2 py-0.5 rounded"
          style={{
            backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`,
            color: 'var(--text-primary)',
          }}
        >
          STEP {n}
        </span>
        <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>
          {date}
        </span>
      </div>
      <h4 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h4>
      <div className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </div>
      <div
        className="mt-3 text-xs font-mono px-2 py-1 rounded inline-block"
        style={{
          backgroundColor: 'var(--bg-secondary)',
          color: 'var(--text-primary)',
        }}
      >
        Running P&amp;L: {pnl}
      </div>
    </div>
  )
}

export function TradeLifecycle() {
  const contentRef = useRef<HTMLDivElement>(null)
  return (
    <div ref={contentRef} className="max-w-5xl">
      <PageHeader
        title="Life of a Trade"
        subtitle="What happens after the bot opens a position — from entry to close, step by step."
      />

      {/* --- Section 1: Framing --- */}
      <Card>
        <P>
          The bot almost never lets a position reach its expiration date. Between the moment a
          trade opens and when the contract expires, the bot checks the position multiple times
          per day and has a series of rules for when to close early, roll to a new contract, or
          let it ride. Most trades are closed within 10-20 days — well before the 30-day
          expiration — because the bot takes profit early. This page walks through what happens
          at each stage.
        </P>
      </Card>

      {/* --- Section 2: Daily Check-In --- */}
      <SectionHeader>The Daily Check-In Schedule</SectionHeader>
      <div
        className="rounded-lg border px-5"
        style={{
          backgroundColor: 'var(--bg-card)',
          borderColor: 'var(--border)',
          borderLeft: '4px solid var(--accent)',
        }}
      >
        <TimeRow time="10:00 AM">
          <strong>Entry evaluation.</strong> The bot scans the market, builds context for each
          watchlist symbol, and decides whether to open new positions. This is the only time new
          trades are opened.
        </TimeRow>
        <TimeRow time="10:45 AM">
          <strong>First position check.</strong> For every open position, the bot fetches current
          option prices and evaluates: has the profit target been hit? Has delta doubled? Is there
          a risk that needs attention?
        </TimeRow>
        <TimeRow time="12:30 PM">
          <strong>Midday check.</strong> Same evaluation as 10:45 — the bot re-checks all open
          positions with updated prices.
        </TimeRow>
        <TimeRow time="2:00 PM">
          <strong>Afternoon check.</strong> Last management pass before the end-of-day sequence
          begins.
        </TimeRow>
        <TimeRow time="3:00 PM">
          <strong>Expiry guard.</strong> Safety sweep specifically looking for positions that
          expire TODAY. Any short option that's in the money gets closed immediately to avoid
          surprise assignment.
        </TimeRow>
        <TimeRow time="3:15 PM">
          <strong>Pre-close observation.</strong> The bot scans for positions approaching
          expiration (within 7 days) and logs warnings. No new orders placed this late — too close
          to market close.
        </TimeRow>
      </div>
      <Callout>
        Between these check-ins, the bot is not watching. If a stock spikes or crashes at 11:15
        AM, the bot won't see it until 12:30. This is a trade-off of the scheduled architecture —
        the bot is not a real-time trading system. The check frequency is designed so that no
        position goes more than ~2 hours unmonitored during market hours.
      </Callout>

      {/* --- Section 3: Three Ways a Trade Ends --- */}
      <SectionHeader>The Three Ways a Trade Ends</SectionHeader>
      <div className="grid gap-4 md:grid-cols-3">
        <Card topAccent="var(--green)">
          <CardTitle>Closed for Profit</CardTitle>
          <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
            Most common outcome (~60-70% of trades)
          </p>
          <P>
            The bot sold a put for $2.50. Over the next 10-15 days, the option's value drops to
            $1.25 — meaning 50% of the premium has decayed. The bot buys it back for $1.25,
            pocketing $1.25 in profit per share ($125 per contract).
          </P>
          <P>
            Why not wait for 100%? Because the last 50% of decay happens in the riskiest part of
            the option's life. Closing at 50% captures most of the income while freeing up capital
            for the next trade.
          </P>
        </Card>
        <Card topAccent="var(--accent)">
          <CardTitle>Rolled to a New Contract</CardTitle>
          <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
            When the stock moves against the position
          </p>
          <P>
            You sold a put at $240 strike when the stock was at $250. The stock drops to $242 —
            your delta has doubled, meaning the position is getting risky. The bot closes the
            current put (buys it back) and simultaneously opens a new one: same or lower strike,
            21-35 days out, collecting fresh premium.
          </P>
          <P>
            A roll is only done for a net credit — meaning you collect more premium on the new
            contract than you pay to close the old one. If the roll would cost money, the bot
            takes a different action instead (accept assignment or close at a loss).
          </P>
        </Card>
        <Card topAccent="var(--red)">
          <CardTitle>Expired or Assigned</CardTitle>
          <p className="text-xs mb-3" style={{ color: 'var(--text-muted)' }}>
            Least common for puts (~10-20%), very rare for calls
          </p>
          <P>
            If the position makes it to expiration without being closed or rolled (unusual — the
            DTE ≤ 7 trigger usually catches it), two things can happen.
          </P>
          <P>
            <strong>OTM</strong> (stock above put strike): the option vanishes from your account —
            it expired worthless, you keep all the premium, nothing to do.{' '}
            <strong>ITM</strong> (stock below put strike): Alpaca automatically assigns you 100
            shares at the strike price. The bot detects the shares in your account on the next
            cycle and transitions to selling covered calls.
          </P>
        </Card>
      </div>

      {/* --- Section 4: Walkthrough --- */}
      <SectionHeader>A Trade From Start to Finish — With Real Numbers</SectionHeader>
      <div className="grid gap-3">
        <StepCard
          n={1}
          date="Day 1 — Monday, March 3"
          title="Entry"
          pnl="+$280 (premium collected)"
          accent="var(--green)"
        >
          <P>
            Apple is trading at $248. IV Rank is 42 (moderate — premiums are decent). The bot
            sells 1 put contract: AAPL $240 strike, April 4 expiration (32 DTE).
          </P>
          <P>
            Premium collected: $2.80/share = <strong>$280</strong>. Alpaca sets aside{' '}
            <strong>$24,000</strong> as collateral (240 × 100).
          </P>
        </StepCard>
        <StepCard
          n={2}
          date="Day 5 — Friday, March 7, 10:00 AM"
          title="First meaningful check — HOLD"
          pnl="+$90 unrealized (+$280 collected, would cost $190 to close)"
          accent="var(--accent)"
        >
          <P>
            Apple is at $251. The option is worth $1.90 (it's decayed from $2.80). Profit so far:
            $2.80 - $1.90 = $0.90/share = $90 (32% of max). The 50% profit target is $1.40.
            Not there yet → <strong>HOLD</strong>.
          </P>
        </StepCard>
        <StepCard
          n={3}
          date="Day 12 — Friday, March 14, 12:30 PM"
          title="Profit target hit — CLOSE"
          pnl="+$165 realized"
          accent="var(--green)"
        >
          <P>
            Apple is at $253. The option is worth $1.15. Profit: $2.80 - $1.15 = $1.65/share =
            $165 (59% of max). 50% target hit → <strong>CLOSE</strong>. Bot buys back the put for
            $1.15.
          </P>
          <P>
            Total P&amp;L: <strong>+$165</strong> (collected $280, paid $115 to close). Collateral
            released. Position closed. Wheel state → IDLE.
          </P>
          <P>
            <strong>Total time in trade: 12 days out of 32 DTE. Capital is free for the next trade.</strong>
          </P>
        </StepCard>
      </div>

      <Callout accent="var(--red)">
        <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
          What if Apple dropped instead?
        </div>
        <P>
          <strong>Day 8:</strong> Apple falls to $241. Delta doubles from -0.25 to -0.50.
        </P>
        <P>
          <strong>Bot evaluates a roll:</strong> close the $240 put (costs $4.20 to buy back),
          open a new $237 put expiring April 11 (collects $4.50). Net credit: $0.30/share.
        </P>
        <P>
          Position is now: short $237 put, 34 DTE, with an additional $30 in premium collected.
          If Apple recovers → close for profit on the new contract. If Apple keeps falling below
          $237 → assigned at $237. Effective cost basis: $237 - $2.80 (original) - $0.30 (roll
          credit) = <strong>$233.90/share</strong>.
        </P>
      </Callout>

      {/* --- Section 5: Bot vs Alpaca --- */}
      <SectionHeader>What the Bot Does vs What Alpaca Does</SectionHeader>
      <div
        className="rounded-lg border overflow-x-auto"
        style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr
              className="text-left text-xs uppercase tracking-wider"
              style={{ color: 'var(--text-muted)' }}
            >
              <th className="px-4 py-3 font-medium">The Bot Decides</th>
              <th className="px-4 py-3 font-medium">Alpaca Executes</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['Which stock, which strike, which expiration', 'Submits the limit order to the exchange'],
              ['When to close early for profit', 'Processes the buy-to-close order'],
              ['When to roll to a new contract', 'Cancels old order, submits new one'],
              ['Nothing — it\'s expiration day', 'Auto-assigns if ITM, auto-expires if OTM'],
              ['Detects new shares after assignment', 'Holds the shares in your account'],
              ['When to sell a covered call', 'Submits the call sell order'],
            ].map(([a, b], i) => (
              <tr
                key={i}
                style={{
                  borderTop: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                <td className="px-4 py-3">{a}</td>
                <td className="px-4 py-3">{b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Callout>
        The bot doesn't need to explicitly handle expiration — Alpaca does that automatically.
        What the bot DOES handle is detecting what happened. After every overnight session, the
        bot checks: "Do I have a short put? Short call? 100 shares? Nothing?" and updates its
        state machine accordingly. The broker is always the source of truth.
      </Callout>

      {/* --- Section 6: State Machine --- */}
      <SectionHeader>The State Machine — How the Bot Tracks Where It Is</SectionHeader>
      <Card>
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <Pill>IDLE</Pill>
          <Arrow />
          <Pill>SHORT_PUT</Pill>
          <Arrow />
          <Pill color="var(--green)">LONG_STOCK</Pill>
          <Arrow />
          <Pill>SHORT_CALL</Pill>
          <Arrow />
          <Pill>IDLE</Pill>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div
            className="p-3 rounded"
            style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
          >
            <div className="font-mono text-xs mb-1" style={{ color: 'var(--text-primary)' }}>
              IDLE
            </div>
            <p className="text-xs">No position. Looking for a put to sell.</p>
          </div>
          <div
            className="p-3 rounded"
            style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
          >
            <div className="font-mono text-xs mb-1" style={{ color: 'var(--text-primary)' }}>
              SHORT_PUT
            </div>
            <p className="text-xs">
              Put is open. Checking every few hours: close for profit? Roll? Hold?
            </p>
          </div>
          <div
            className="p-3 rounded"
            style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
          >
            <div className="font-mono text-xs mb-1" style={{ color: 'var(--text-primary)' }}>
              LONG_STOCK
            </div>
            <p className="text-xs">
              Own 100 shares (after assignment). Looking for a call to sell. If the put expired
              worthless or was closed early instead, skip this state and return to IDLE.
            </p>
          </div>
          <div
            className="p-3 rounded"
            style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
          >
            <div className="font-mono text-xs mb-1" style={{ color: 'var(--text-primary)' }}>
              SHORT_CALL
            </div>
            <p className="text-xs">
              Call is open. Same management checks as the put side. Called away → IDLE. Expired
              worthless or closed early → back to LONG_STOCK.
            </p>
          </div>
        </div>
      </Card>
      <P>
        The bot doesn't track transitions explicitly — it doesn't say "I was assigned, therefore
        move to LONG_STOCK." Instead, every cycle it looks at what's actually in the Alpaca
        account: "Do I see a short put? That's SHORT_PUT state. Do I see 100 shares? That's
        LONG_STOCK." This means the bot automatically handles anything that happens between
        cycles — assignment, expiration, even if you manually close a position through Alpaca's
        dashboard. The bot just reads the current reality and adapts.
      </P>

      {/* --- Section 7: Spread Lifecycle --- */}
      <SectionHeader>Spread Lifecycle — Simpler Than the Wheel</SectionHeader>
      <P>
        Spreads (bull put, bear call, iron condor, long call vertical) have a simpler lifecycle
        than the wheel because they never involve owning stock. A spread opens, gets managed, and
        closes. That's it.
      </P>
      <Card>
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <Pill>IDLE</Pill>
          <Arrow />
          <Pill>PENDING_OPEN</Pill>
          <Arrow />
          <Pill color="var(--green)">OPEN</Pill>
          <Arrow />
          <Pill>PENDING_CLOSE</Pill>
          <Arrow />
          <Pill>CLOSED</Pill>
          <Arrow />
          <Pill>IDLE</Pill>
        </div>
        <ul className="space-y-2 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <li className="flex gap-2">
            <span style={{ color: 'var(--accent)' }}>•</span>
            <span>
              <strong>PENDING_OPEN:</strong> Order submitted but not yet filled. The bot waits —
              it won't act on a position that might not exist yet.
            </span>
          </li>
          <li className="flex gap-2">
            <span style={{ color: 'var(--accent)' }}>•</span>
            <span>
              <strong>OPEN:</strong> Order filled, position is live. Management checks run every
              cycle: profit target hit? Stop loss breached? DTE running low?
            </span>
          </li>
          <li className="flex gap-2">
            <span style={{ color: 'var(--accent)' }}>•</span>
            <span>
              <strong>PENDING_CLOSE:</strong> Close order submitted. The bot waits for fill
              confirmation before marking the position as done.
            </span>
          </li>
          <li className="flex gap-2">
            <span style={{ color: 'var(--accent)' }}>•</span>
            <span>
              <strong>CLOSED:</strong> Position is done. P&amp;L is calculated. The strategy
              resets to IDLE for the next opportunity.
            </span>
          </li>
        </ul>
      </Card>
      <Callout>
        The extra PENDING states exist because options orders don't always fill instantly. A limit
        order might sit for hours or never fill at all. The bot tracks this explicitly so it never
        tries to manage a position that hasn't actually opened, or re-close a position it's
        already closing.
      </Callout>

      {/* --- Footer --- */}
      <div className="mt-10">
        <P>
          For how the bot decides WHICH trade to open, see the How Claude Decides page. For the
          specific rules each strategy follows, see Strategies. For the safety rules that protect
          against bad trades, see Guardrails.
        </P>
        <p
          className="text-xs text-center mt-6 mb-4"
          style={{ color: 'var(--text-muted)' }}
        >
          This bot runs on paper trading accounts. No real capital is at risk.
        </p>
      </div>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
