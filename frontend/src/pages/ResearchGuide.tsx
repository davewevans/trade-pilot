import { useRef } from 'react'
import { PageAudioPlayer } from '../components/shared/PageAudioPlayer'

// ── Shared helpers ────────────────────────────────────────────────────────────

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

function Arrow() {
  return (
    <div className="flex justify-center my-1" aria-hidden="true">
      <span style={{ color: 'var(--text-muted)' }}>↓</span>
    </div>
  )
}

function Card({
  title,
  body,
  accentColor,
}: {
  title: string
  body: React.ReactNode
  accentColor?: string
}) {
  return (
    <div
      className="p-4 rounded-lg border"
      style={{
        backgroundColor: 'var(--bg-card)',
        borderColor: 'var(--border)',
        borderTop: accentColor ? `3px solid ${accentColor}` : undefined,
      }}
    >
      <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
        {title}
      </h3>
      <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {body}
      </p>
    </div>
  )
}

function BulletList({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="space-y-1.5 mt-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2 text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          <span style={{ color: 'var(--accent)', flexShrink: 0 }}>•</span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

// ── Sunday job steps ──────────────────────────────────────────────────────────

const JOB_STEPS = [
  {
    num: 1,
    label: 'Liquidity scan',
    body: 'Queries ORATS historical options data for every symbol in the candidate universe, writing fresh liquidity snapshots.',
  },
  {
    num: 2,
    label: 'Liquidity rescore',
    body: 'Aggregates the last 30 days of snapshots, recomputes composite scores, and re-assigns tiers A–D across the universe.',
  },
  {
    num: 3,
    label: 'Backtest sweep',
    body: 'Runs simulated trades across symbols × strategies × 3 years of history in chunks (resumable across weeks). Aggregates win rates into per-symbol and per-regime stats.',
  },
  {
    num: 4,
    label: 'Watchlist recommendations',
    body: 'Compares watchlist members against the candidate universe on the combined scoring formula. Writes pending add/remove recommendations.',
  },
  {
    num: 5,
    label: 'Run summary',
    body: 'Writes research_last_run.json with stats, durations, and any per-phase errors. The Research dashboard "Last run" badge reads this file.',
  },
]

// ── Tier table data ───────────────────────────────────────────────────────────

const TIERS = [
  { tier: 'A', mult: '1.20×', dot: '#22c55e', desc: 'Top quartile. Ranking boost.' },
  { tier: 'B', mult: '1.00×', dot: '#3b82f6', desc: 'Second quartile. Neutral baseline. Also assigned when data is insufficient (<5 observations).' },
  { tier: 'C', mult: '0.80×', dot: '#eab308', desc: 'Third quartile. Mild ranking penalty.' },
  { tier: 'D', mult: '0.00×', dot: '#ef4444', desc: 'Bottom quartile OR below absolute liquidity floor (too-wide spreads / too-low open interest). Candidate rejected entirely.' },
]

// ── Win-rate table data ───────────────────────────────────────────────────────

const WIN_RATES = [
  { range: '≥ 70%', mult: '1.30×', label: 'Strong', dot: '#22c55e' },
  { range: '60–70%', mult: '1.15×', label: 'Good', dot: '#4ade80' },
  { range: '50–60%', mult: '1.00×', label: 'Neutral', dot: '#3b82f6' },
  { range: '40–50%', mult: '0.85×', label: 'Weak', dot: '#eab308' },
  { range: '30–40%', mult: '0.70×', label: 'Poor', dot: '#f97316' },
  { range: '< 30%', mult: '0.00×', label: 'Reject', dot: '#ef4444' },
]

// ── Component ─────────────────────────────────────────────────────────────────

export function ResearchGuide() {
  const contentRef = useRef<HTMLDivElement>(null)

  return (
    <div ref={contentRef} className="max-w-5xl">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="mb-8">
        <h1 className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          Research System Guide
        </h1>
        <p className="text-sm mt-2 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          The bot spends its off-hours doing research so you don't have to. This page explains what
          the research system observes, how it scores symbols, when it recommends watchlist changes,
          and how all of that flows into the decisions the bot makes during market hours.
        </p>
      </div>

      {/* ── Section 1: Why this exists ──────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Why This Exists</SectionHeader>
        <div
          className="p-5 rounded-lg border space-y-3"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            The bot trades for you because you don't have time to research manually. But every bot
            is only as good as its candidate pool — if it's trading symbols with bad option
            liquidity or poor historical fit for the strategy, no amount of Claude's reasoning will
            save the trade. Someone has to do that research. Before the research system, that someone
            was you.
          </p>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            The research system runs three Sunday jobs that collect and analyze data automatically,
            then feed scores into every candidate-ranking decision during the week. You don't have
            to read any of it — but the dashboard is there when you want to peek, and the
            /recommendations page shows exactly what the bot thinks you should change about the
            watchlists.
          </p>
        </div>
      </section>

      {/* ── Section 2: The three things it does ────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>The Three Things It Does</SectionHeader>
        <div className="grid gap-3 md:grid-cols-3">
          <Card
            title="Liquidity Scoring"
            accentColor="var(--accent)"
            body="Every trading cycle and every Sunday, the bot samples the option chain for each candidate symbol and measures how tradeable the contracts actually are — bid-ask spread width, open interest, and estimated slippage. Symbols with consistently tight, liquid options get ranked higher when multiple candidates compete for an entry."
          />
          <Card
            title="Historical Win Rates"
            accentColor="var(--accent)"
            body="Every Sunday, the bot runs simulated backtests across the candidate universe for each strategy type, using 3 years of ORATS historical data. The results aggregate into per-symbol and per-regime win rates. Strategies with strong historical performance on a symbol get a ranking boost; ones with persistently poor performance get rejected."
          />
          <Card
            title="Watchlist Recommendations"
            accentColor="var(--accent)"
            body="The bot compares current watchlist members against candidates in the broader S&P 500 universe. Symbols outperforming on both liquidity and win rate get recommended as additions. Current members that have deteriorated over 12+ weeks get recommended for removal. Nothing changes automatically — you approve or reject each suggestion on the /recommendations page."
          />
        </div>
      </section>

      {/* ── Section 3: Liquidity scoring ───────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Liquidity Scoring — In Detail</SectionHeader>

        {/* Steps */}
        <div
          className="p-5 rounded-lg border mb-5"
          style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
        >
          {JOB_STEPS.slice(0, 2).map((s, i) => (
            <div key={s.num}>
              <div
                className="p-3 rounded-lg border flex gap-3 items-start"
                style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
              >
                <StepBadge n={s.num} />
                <div>
                  <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                    {s.label}
                  </div>
                  <p className="text-sm leading-relaxed mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.body}
                  </p>
                </div>
              </div>
              {i === 0 && <Arrow />}
            </div>
          ))}
        </div>

        {/* Sub-metrics */}
        <div
          className="p-4 rounded-lg border mb-4"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <h3 className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
            Four sub-metrics, weighted into one composite
          </h3>
          <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text-secondary)' }}>
            Each observation collects four measurements, each assigned a fixed weight:
          </p>
          <div
            className="rounded border divide-y"
            style={{ borderColor: 'var(--border)' }}
          >
            {[
              { metric: 'Bid-ask spread', weight: '40%', note: 'Tighter is better. Inverse-ranked.' },
              { metric: 'Open interest at target strikes', weight: '25%', note: 'More OI means more liquid contracts.' },
              { metric: 'Volume-to-OI ratio', weight: '15%', note: 'Distinguishes active trading from stale open interest.' },
              { metric: 'Estimated slippage', weight: '20%', note: 'Modeled fill cost vs. mid-price. Inverse-ranked.' },
            ].map((row) => (
              <div
                key={row.metric}
                className="px-4 py-2.5 grid gap-2 md:grid-cols-[1fr_4rem_1fr]"
                style={{ borderColor: 'var(--border)' }}
              >
                <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                  {row.metric}
                </span>
                <span
                  className="text-sm font-mono font-semibold"
                  style={{ color: 'var(--accent)' }}
                >
                  {row.weight}
                </span>
                <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                  {row.note}
                </span>
              </div>
            ))}
          </div>
          <p className="text-sm leading-relaxed mt-3" style={{ color: 'var(--text-secondary)' }}>
            Each metric is normalized via a <em>log-percentile rank</em> before being weighted. Log
            scale matters because SPY might have 100× more open interest than a thin mid-cap —
            without it, everything except SPY would look identical. The final composite is 0–100.
          </p>
          <p className="text-sm leading-relaxed mt-2" style={{ color: 'var(--text-secondary)' }}>
            Confidence levels are based on snapshot count: <strong style={{ color: 'var(--text-primary)' }}>high</strong> requires 30+
            observations, <strong style={{ color: 'var(--text-primary)' }}>low</strong> requires 5–29, and{' '}
            <strong style={{ color: 'var(--text-primary)' }}>none</strong> means fewer than 5 observations exist. Low and none confidence always return a 1.0× multiplier regardless of tier — thin data never drives decisions.
          </p>
        </div>

        {/* Tier table */}
        <h3
          className="text-xs uppercase tracking-wider font-semibold mb-3"
          style={{ color: 'var(--text-muted)' }}
        >
          Tier Reference
        </h3>
        <div
          className="rounded-lg border divide-y"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          {TIERS.map((t) => (
            <div
              key={t.tier}
              className="px-4 py-3 grid gap-2 md:grid-cols-[3rem_4rem_1fr]"
              style={{ borderColor: 'var(--border)' }}
            >
              <div className="flex items-center gap-2">
                <span
                  style={{
                    display: 'inline-block',
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: t.dot,
                    flexShrink: 0,
                  }}
                />
                <span className="font-mono font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                  {t.tier}
                </span>
              </div>
              <span
                className="font-mono text-sm font-semibold"
                style={{ color: t.tier === 'D' ? '#ef4444' : 'var(--text-primary)' }}
              >
                {t.mult}
              </span>
              <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
                {t.desc}
              </span>
            </div>
          ))}
        </div>

        <p className="text-sm leading-relaxed mt-3" style={{ color: 'var(--text-secondary)' }}>
          Tiers are quartile cutoffs computed <em>per strategy type</em> so iron condors rank
          against iron condors, not against wheel puts. A symbol can be Tier A for wheel_csp and
          Tier C for iron_condor — the strategies measure different strikes and DTE ranges.
        </p>
      </section>

      {/* ── Section 4: Historical win rates ────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Historical Win Rates — In Detail</SectionHeader>

        <div
          className="p-4 rounded-lg border mb-4"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text-secondary)' }}>
            Unlike liquidity (which samples live market data), win rates come from simulated
            backtests. The Sunday sweep runs your existing backtest engine across symbols ×
            strategies × 3 years of ORATS historical data. Each completed trade is stored in a raw
            trades table; two parallel aggregations roll those up into actionable stats.
          </p>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            <strong style={{ color: 'var(--text-primary)' }}>symbol_strategy_stats</strong> pools
            across all regimes — "does AAPL work consistently for bull put spreads?"{' '}
            <strong style={{ color: 'var(--text-primary)' }}>regime_strategy_stats</strong> pools
            across all symbols — "does iron condor NEUTRAL-only routing actually pay off?" A
            single three-dimensional matrix (symbol × strategy × regime) would be more granular but
            produces roughly 4 trades per bucket on the available data — noise with attribution.
            Two marginals is more honest.
          </p>
          <p className="text-sm leading-relaxed mt-2" style={{ color: 'var(--text-secondary)' }}>
            Confidence levels: <strong style={{ color: 'var(--text-primary)' }}>high</strong> requires 30+ backtest trades; <strong style={{ color: 'var(--text-primary)' }}>low</strong> requires 10–29. Below 10 trades the multiplier is always 1.0 (neutral).
          </p>
        </div>

        {/* Win-rate table */}
        <h3
          className="text-xs uppercase tracking-wider font-semibold mb-3"
          style={{ color: 'var(--text-muted)' }}
        >
          Win-Rate Multiplier Reference
        </h3>
        <div
          className="rounded-lg border divide-y"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          {WIN_RATES.map((w) => (
            <div
              key={w.range}
              className="px-4 py-3 grid gap-2 md:grid-cols-[5rem_4rem_5rem_1fr]"
              style={{ borderColor: 'var(--border)' }}
            >
              <span className="font-mono text-sm" style={{ color: 'var(--text-primary)' }}>
                {w.range}
              </span>
              <span
                className="font-mono text-sm font-semibold"
                style={{ color: w.label === 'Reject' ? '#ef4444' : 'var(--text-primary)' }}
              >
                {w.mult}
              </span>
              <span className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    backgroundColor: w.dot,
                    flexShrink: 0,
                  }}
                />
                {w.label}
              </span>
              <span />
            </div>
          ))}
        </div>

        {/* Hard floor callout */}
        <div
          className="mt-4 p-4 rounded-lg border"
          style={{
            backgroundColor: 'color-mix(in srgb, #ef4444 8%, var(--bg-card))',
            borderColor: 'color-mix(in srgb, #ef4444 30%, var(--border))',
          }}
        >
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            <strong style={{ color: 'var(--text-primary)' }}>Both floors (Tier D liquidity OR
            reject-tier win rate) skip the candidate entirely</strong> without even sending it to
            Claude. This is the system's only automatic rejection mechanism. Ranking nudges are
            soft; floors are hard.
          </p>
        </div>
      </section>

      {/* ── Section 5: How multipliers combine ─────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>How the Multipliers Combine</SectionHeader>

        {/* Formula */}
        <div
          className="p-5 rounded-lg border mb-5 text-center"
          style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
        >
          <div
            className="font-mono text-base font-semibold"
            style={{ color: 'var(--text-primary)' }}
          >
            final_score = raw_score × liquidity_mult × winrate_mult
          </div>
          <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
            Applied during pre-check candidate ranking, before Claude sees the shortlist.
          </p>
        </div>

        {/* Examples */}
        <div className="grid gap-3 md:grid-cols-2 mb-4">
          <div
            className="p-4 rounded-lg border"
            style={{
              backgroundColor: 'var(--bg-card)',
              borderColor: 'var(--border)',
              borderLeft: '3px solid #22c55e',
            }}
          >
            <div className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
              Example — stacked boost
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              AAPL bull put spread candidate. Tier A liquidity (1.20×), 72% historical win rate
              (1.30×, strong). Raw score 0.20. Final score{' '}
              <span className="font-mono" style={{ color: 'var(--text-primary)' }}>
                0.20 × 1.20 × 1.30 = 0.312
              </span>
              . The candidate ranks 56% higher than its raw score alone.
            </p>
          </div>
          <div
            className="p-4 rounded-lg border"
            style={{
              backgroundColor: 'var(--bg-card)',
              borderColor: 'var(--border)',
              borderLeft: '3px solid #ef4444',
            }}
          >
            <div className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
              Example — hard floor
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              XYZ iron condor candidate. Tier B liquidity (1.00×), 25% historical win rate with
              high confidence (reject floor, 0.00×). Final score{' '}
              <span className="font-mono" style={{ color: '#ef4444' }}>
                0.20 × 1.00 × 0.00 = 0.00
              </span>
              . The candidate is skipped with reasoning 'below_winrate_floor'. Claude never sees
              it.
            </p>
          </div>
        </div>

        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Liquidity multipliers range from 0.80× (Tier C) to 1.20× (Tier A) for active tiers.
          Win-rate multipliers range from 0.70× (poor) to 1.30× (strong). Both are 1.0× when
          data is insufficient — thin data never drives decisions. Both have independent kill
          switches if you need to disable either scoring layer without a redeploy.
        </p>
      </section>

      {/* ── Section 6: Watchlist recommendations ──────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Watchlist Recommendations</SectionHeader>

        <div
          className="p-4 rounded-lg border mb-4"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            Every Sunday, after liquidity scoring and backtest stats finish, the recommender scores
            every candidate against every current watchlist member using a combined metric:{' '}
            <span className="font-mono text-xs" style={{ color: 'var(--text-primary)' }}>
              liq_composite × 0.6 + win_rate_bonus + novelty_bonus
            </span>
            . Current members get an additional +15 incumbent bonus to prevent churn. Candidates
            outscoring current members on this formula (and passing all data-sufficiency gates) get
            suggested as additions. Current members that score below the removal threshold after
            12+ weeks of observed data get suggested for removal.
          </p>
          <p className="text-sm leading-relaxed mt-2" style={{ color: 'var(--text-secondary)' }}>
            Win-rate bonuses: strong = +30, good = +20, neutral = +10, weak/poor/reject = +0.
            Novelty bonus: +10 if the symbol isn't already in any watchlist.
          </p>
        </div>

        {/* Rules */}
        <div
          className="p-4 rounded-lg border mb-4"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <div className="font-semibold text-sm mb-2" style={{ color: 'var(--text-primary)' }}>
            What the recommender will and won't do
          </div>
          <BulletList
            items={[
              'Maximum 5 add recommendations and 5 remove recommendations per watchlist per week.',
              'Won\'t recommend removing a current member without 12+ weeks of observation data (configurable via RESEARCH_REMOVE_MIN_WEEKS_OBSERVED).',
              'Won\'t recommend adding a candidate without high-confidence liquidity data AND at least low-confidence win-rate data.',
              'Won\'t recommend anything in Tier C or D liquidity — only Tier A and B candidates qualify.',
              "Won't recommend a win-rate multiplier below 1.0 — neutral, good, or strong only.",
              'Refuses to let any watchlist drop below 5 members, even if you try to approve the remove.',
              'Approvals happen on the /recommendations page. The watchlist.json updates only after you click Confirm.',
            ]}
          />
        </div>

        <div
          className="p-4 rounded-lg border"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          <div className="font-semibold text-sm mb-1" style={{ color: 'var(--text-primary)' }}>
            What to expect on day 1
          </div>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            For the first 6–8 weeks after the system ships, most recommendations will say "no
            change" or "considered but rejected, insufficient data." This is correct behavior —
            the gates above refuse to act on thin data. Coverage ramps up as the Sunday sweep
            fills out the universe.
          </p>
        </div>
      </section>

      {/* ── Section 7: The Sunday job ───────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>The Sunday Job</SectionHeader>

        <div
          className="p-5 rounded-lg border mb-4"
          style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
        >
          {JOB_STEPS.map((s, i) => (
            <div key={s.num}>
              <div
                className="p-3 rounded-lg border flex gap-3 items-start"
                style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
              >
                <StepBadge n={s.num} />
                <div className="min-w-0">
                  <div className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                    {s.label}
                  </div>
                  <p className="text-sm leading-relaxed mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.body}
                  </p>
                </div>
              </div>
              {i < JOB_STEPS.length - 1 && <Arrow />}
            </div>
          ))}
        </div>

        <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          Total runtime: 10–30 minutes depending on chunk size and ORATS cache warmth. Each stage
          is independently wrapped — if one fails, the others still run. You'll see a "Last run"
          badge on the Research dashboard with the most recent timestamp and status.
        </p>
      </section>

      {/* ── Section 8: Kill switches ────────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Kill Switches</SectionHeader>

        <p className="text-sm leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
          If something goes wrong with either scoring layer, you can disable it without a
          redeploy. Two environment variables, independent of each other.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div
            className="p-4 rounded-lg border"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <div
              className="font-mono text-xs font-semibold mb-2"
              style={{ color: 'var(--accent)' }}
            >
              RESEARCH_SCORE_MULTIPLIER_ENABLED
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              Disables the liquidity multiplier. All candidates get 1.0× regardless of tier. The
              Tier D hard floor is <strong style={{ color: 'var(--text-primary)' }}>not</strong>{' '}
              enforced — symbols with poor liquidity won't be auto-rejected. Use if liquidity
              scores look wrong or are causing unexpected candidate selection.
            </p>
          </div>
          <div
            className="p-4 rounded-lg border"
            style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
          >
            <div
              className="font-mono text-xs font-semibold mb-2"
              style={{ color: 'var(--accent)' }}
            >
              RESEARCH_WINRATE_MULTIPLIER_ENABLED
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              Disables the win-rate multiplier. All candidates get 1.0× regardless of historical
              performance. The reject-tier floor is{' '}
              <strong style={{ color: 'var(--text-primary)' }}>not</strong> enforced. Use if
              backtest stats look wrong or the sweep produced bad data.
            </p>
          </div>
        </div>

        <p className="text-sm leading-relaxed mt-4" style={{ color: 'var(--text-secondary)' }}>
          Both kill switches write <code className="font-mono text-xs">'disabled'</code> into the
          research metadata logged with every decision, so you can audit exactly when they were
          off. Flipping either switch does not lose data — the Sunday jobs keep running and
          accumulating observations. You can re-enable at any time.
        </p>
      </section>

      {/* ── Section 9: Where to look ─────────────────────────────────────────── */}
      <section className="mb-10">
        <SectionHeader>Where to Look in the Dashboard</SectionHeader>

        <div
          className="rounded-lg border divide-y"
          style={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)' }}
        >
          {[
            {
              path: '/research',
              desc: 'Liquidity heatmap, win-rate heatmap, combined scores, per-symbol deep dive. Coverage stats show last sweep run and data breadth.',
            },
            {
              path: '/recommendations',
              desc: 'Pending watchlist recommendations to approve or reject. The only place where watchlist.json can change.',
            },
            {
              path: '/decisions',
              desc: 'Decision log. Research metadata (raw_score, multipliers, final_score) is included in the JSON payload per decision — visible in the Reasoning column.',
            },
            {
              path: '/backtest',
              desc: 'Operator-run one-off backtests. Separate from the automated Sunday sweep — these don\'t feed the /research win-rate stats.',
            },
          ].map((row) => (
            <div
              key={row.path}
              className="px-4 py-3 grid gap-1 md:gap-4 md:grid-cols-[10rem_1fr]"
              style={{ borderColor: 'var(--border)' }}
            >
              <code
                className="font-mono text-sm font-semibold"
                style={{ color: 'var(--accent)' }}
              >
                {row.path}
              </code>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {row.desc}
              </p>
            </div>
          ))}
        </div>
      </section>

      <PageAudioPlayer contentRef={contentRef} />
    </div>
  )
}
