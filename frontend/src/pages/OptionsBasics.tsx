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
}: {
  children: React.ReactNode
  accent?: string
}) {
  return (
    <div
      className="p-5 rounded-lg border"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderLeft: accent ? `4px solid ${accent}` : undefined,
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

function Badge({
  children,
  color,
}: {
  children: React.ReactNode
  color: string
}) {
  return (
    <span
      className="inline-block text-[10px] font-mono px-2 py-0.5 rounded-full mt-2"
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

export function OptionsBasics() {
  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Options Basics"
        subtitle="Options trading explained without the jargon. Start here if you're new."
      />

      {/* --- Section 1: Insurance Analogy --- */}
      <Card>
        <CardTitle>The Insurance Analogy</CardTitle>
        <P>
          Your car insurance company collects $150/month from you. Most months, nothing happens —
          they keep the money. Occasionally something bad happens and they pay out. Over time,
          the premiums they collect exceed the claims they pay. That's how they make money.
        </P>
        <P>
          Options trading works the same way. Someone pays a premium. Someone else collects it.
          One side is betting something will happen. The other side is betting it won't. This bot
          is the insurance company — it sells options and collects premiums.
        </P>
      </Card>

      {/* --- Section 2: Calls and Puts --- */}
      <SectionHeader>Calls and Puts — The Two Types</SectionHeader>
      <div className="grid gap-4 md:grid-cols-2">
        <Card accent="var(--accent)">
          <CardTitle>Put — Insurance Against a Stock Dropping</CardTitle>
          <P>
            A put gives someone the right to SELL you 100 shares at a specific price (the strike),
            no matter how far the stock falls. You collect premium for taking that risk.
          </P>
          <P>
            Apple is at $250. You sell a put with a $240 strike for $3/share ($300 total). If Apple
            stays above $240, you keep $300. If Apple drops to $220, you buy 100 shares at $240 —
            $20 above market price.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Call — Insurance Against Missing a Rally</CardTitle>
          <P>
            A call gives someone the right to BUY 100 shares from you at a specific price. You
            collect premium for capping your upside.
          </P>
          <P>
            You own 100 shares of Apple at $250. You sell a call at $260 for $4/share ($400). If
            Apple stays below $260, you keep the shares and the $400. If Apple rockets to $280, you
            sell at $260 — you miss the extra $20 of upside, but you still profit.
          </P>
        </Card>
      </div>
      <Callout>
        The bot starts with cash, not stock. It sells puts first — promising to buy shares if the
        stock drops. Most of the time (70-80%), the stock stays above the strike and the bot just
        keeps the premium. When it does get assigned, it's on a stock the bot was willing to own
        anyway.
      </Callout>

      {/* --- Section 3: The Numbers That Matter --- */}
      <SectionHeader>The Numbers That Matter</SectionHeader>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardTitle>Delta — Probability of Getting Assigned</CardTitle>
          <P>
            Delta tells you roughly how likely the option is to end up "in the money." The bot
            sells puts with delta around -0.25, which means about a 25% chance the stock drops
            below the strike. That's a 75% chance of keeping the premium free and clear.
          </P>
        </Card>
        <Card>
          <CardTitle>DTE — How Long the Contract Lasts</CardTitle>
          <P>
            DTE is "days to expiration." The bot targets 21-35 days. Why? Because options lose
            value fastest in this window. Think of it like insurance — a policy with 30 days left
            is losing value rapidly because there's less and less time for something to happen. The
            bot sells in this fast-decay zone to maximize income.
          </P>
        </Card>
        <Card>
          <CardTitle>IV Rank — How Scared Is the Market?</CardTitle>
          <P>
            IV Rank (Implied Volatility Rank) measures how expensive options are right now compared
            to the past year. When the market is nervous, insurance premiums go up — just like car
            insurance after a hurricane. The bot waits for IVR ≥ 30 before selling, because that
            means premiums are above average. Selling when premiums are cheap isn't worth the risk.
          </P>
        </Card>
        <Card>
          <CardTitle>Premium — The Money You Collect</CardTitle>
          <P>
            Premium is the price the option buyer pays you. It's deposited in your account
            immediately when you sell. If the option expires worthless (the good outcome), you keep
            100% of it. The bot's profit comes from collecting premium month after month, not from
            picking stocks that go up.
          </P>
        </Card>
      </div>

      {/* --- Section 4: The Wheel --- */}
      <SectionHeader>The Wheel — How This Bot Makes Money</SectionHeader>
      <PhaseStepper
        steps={[
          'Cash',
          'Sell Put',
          '(assigned?) Own Stock',
          'Sell Call',
          '(called away?)',
          'Cash',
        ]}
      />
      <div className="grid gap-3">
        <Card>
          <P>
            <strong>1. Start with $25,000 cash.</strong> Apple is at $240.
          </P>
          <P>
            <strong>2. Sell a put at $230 strike, collect $2.50/share ($250).</strong> Alpaca sets
            aside $23,000 as collateral.
          </P>
          <P>
            <strong>3. Apple stays above $230.</strong> Put expires worthless. You keep $250.
            Collateral released. Repeat.
          </P>
          <P>
            <strong>4. (Or) Apple drops to $220.</strong> You're assigned — you now own 100 shares
            at $230. Your effective cost is $227.50 ($230 minus the $2.50 premium). Yes, you're
            down on paper, but you own a stock you wanted.
          </P>
          <P>
            <strong>
              5. Sell a covered call at $240 strike, collect $3/share ($300).
            </strong>{' '}
            If Apple recovers above $240, your shares are sold at $240 for a $12.50/share profit
            plus the $300 call premium.
          </P>
          <P>
            <strong>6. Back to cash.</strong> Cycle repeats.
          </P>
        </Card>
      </div>
      <Callout>
        Most months, the cycle stays at steps 2-3: sell put, collect premium, repeat. Assignment
        (step 4) happens about 20-30% of the time, and it's not a failure — it's the wheel working
        as designed. The bot has been assigned on a stock it was already willing to own.
      </Callout>

      {/* --- Section 5: Spreads --- */}
      <SectionHeader>Spreads — Defined-Risk Strategies</SectionHeader>
      <P>
        The bot also runs four "spread" strategies that use two or more options simultaneously.
        Spreads cap your maximum loss at the time you enter the trade — you always know the
        worst-case scenario before you open the position.
      </P>
      <div className="grid gap-3 md:grid-cols-2 mt-3">
        <Card>
          <CardTitle>Bull Put Spread</CardTitle>
          <P>
            Sell a put + buy a cheaper put below it. You profit if the stock stays flat or goes up.
            Max loss is capped by the long put.
          </P>
          <Badge color="var(--green)">Credit — you collect premium</Badge>
        </Card>
        <Card>
          <CardTitle>Bear Call Spread</CardTitle>
          <P>
            Sell a call + buy a cheaper call above it. You profit if the stock stays flat or goes
            down. Same defined-risk structure as the bull put, but bearish.
          </P>
          <Badge color="var(--green)">Credit — you collect premium</Badge>
        </Card>
        <Card>
          <CardTitle>Iron Condor</CardTitle>
          <P>
            A bull put spread + a bear call spread together. You profit if the stock stays in a
            range. Four legs, but max loss is still defined.
          </P>
          <Badge color="var(--green)">Credit — you collect premium</Badge>
        </Card>
        <Card>
          <CardTitle>Long Call Vertical</CardTitle>
          <P>
            Buy a call + sell a cheaper call above it. You profit if the stock goes up. This is the
            one strategy where the bot PAYS premium instead of collecting it — used only when
            options are cheap.
          </P>
          <Badge color="var(--red)">Debit — you pay premium</Badge>
        </Card>
      </div>
      <Callout>
        For detailed entry criteria, management rules, and how the bot picks which strategy to use,
        see the Strategies page.
      </Callout>

      {/* --- Section 6: What Makes This Different --- */}
      <SectionHeader>What Makes This Bot Different From Stock Trading</SectionHeader>
      <div className="grid gap-3">
        <Card accent="var(--accent)">
          <CardTitle>Income, not growth.</CardTitle>
          <P>
            Stock traders buy low and sell high. This bot sells insurance contracts and collects
            premiums. The goal is steady monthly income, not big one-time gains.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Time is on your side.</CardTitle>
          <P>
            When you buy stocks, time is neutral. When you sell options, every day that passes
            makes your position more profitable (the option loses value through time decay).
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>You get paid to wait.</CardTitle>
          <P>
            Instead of setting a limit order to buy Apple at $230 and waiting for free, you sell a
            put at $230 and get paid $250 while you wait. If Apple never drops there, you keep the
            $250. If it does, you buy at $230 like you planned — but you still keep the $250.
          </P>
        </Card>
        <Card accent="var(--accent)">
          <CardTitle>Risk is defined.</CardTitle>
          <P>
            Spread strategies cap your maximum loss before you enter. You always know the worst
            case.
          </P>
        </Card>
      </div>

      {/* --- Section 7: Common Fears --- */}
      <SectionHeader>Common Fears (and Why They're Manageable)</SectionHeader>
      <div
        className="rounded-lg border divide-y"
        style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
      >
        {[
          {
            q: 'What if the stock crashes?',
            a: 'For cash-secured puts: you buy the stock at the strike price. It hurts, but you picked a stock you were willing to own, and you have the premium cushion. For spreads: your loss is capped at the spread width. The circuit breaker halts all new trading if the portfolio drops 3% in a day or 5% in a week.',
          },
          {
            q: 'What if I lose all my money?',
            a: 'The bot never risks more than 10% of buying power on any single position. Spreads cap max loss at 1-2% per trade. Five concurrent wheel positions max. The bot literally cannot bet the whole account on one trade.',
          },
          {
            q: 'Isn\'t this just gambling?',
            a: 'Insurance companies aren\'t gambling — they have a statistical edge and they manage risk. The bot sells options at a 70-80% win rate, keeps position sizes small, closes winners early (at 50% profit), and has hard rules that prevent emotional decisions. It\'s systematic, not speculative.',
          },
          {
            q: 'What are the Greeks? Do I need to understand them?',
            a: 'The Greeks (delta, gamma, theta, vega) are just measurements of how an option\'s price changes. You already understand the important one: delta is roughly the probability of assignment. The bot handles the rest. As you watch it make decisions, you\'ll naturally absorb what theta (time decay) and vega (volatility sensitivity) mean from the reasoning it provides.',
          },
          {
            q: 'What\'s the worst that can happen?',
            a: 'Worst case: every stock in the watchlist drops simultaneously, all puts get assigned, and all spreads hit max loss. With the current guardrails, this means roughly 60% of capital is at risk — painful but not a wipeout. The 15% drawdown lock halts the bot long before this scenario fully plays out.',
          },
        ].map((item, i) => (
          <div key={i} className="p-5" style={{ borderColor: 'var(--border)' }}>
            <div className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
              {item.q}
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              {item.a}
            </p>
          </div>
        ))}
      </div>

      {/* --- Footer --- */}
      <p
        className="text-xs text-center mt-10 mb-4"
        style={{ color: 'var(--text-muted)' }}
      >
        This bot runs on paper trading accounts. No real capital is at risk. It's a learning tool
        built to understand options through building and observing.
      </p>
    </div>
  )
}
