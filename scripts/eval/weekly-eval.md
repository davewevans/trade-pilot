# Weekly evaluation — methodology (the self-improving loop's brain)

Instruction set the weekly Cowork scheduled task follows. It runs fresh (no memory of prior
runs), reads the week's inputs from `eval-inbox/`, grounds everything in the CURRENT repo code,
and writes runnable tickets to `tickets/ready/` for the nightly Claude Code runner.

**Read `tickets/README.md` first** for the ticket format and the green/red scope rails — this
doc does not repeat them.

## Prime directive

Improve the bot's decision quality and fix real defects — grounded in DATA and CODE, not vibes.
**"No ticket this week" is a valid, expected outcome.** Do not manufacture work. Every ticket
must trace to concrete evidence (DB snapshot or a Hermes issue) AND be confirmed
not-already-fixed in the current code.

## Inputs (all under `C:\Users\davew\repos\trade-pilot\`)

- `eval-inbox/tp_snap_YYYY-MM-DD.db` — newest one = this week's DB truth.
- `eval-inbox/hermes-issues.json` — open Hermes suggestions (GitHub issues).
- The repo code — the source of truth for what is already implemented.
- `knowledge/eval_reports/` — your own prior weekly reports (rolling baseline; may be empty).

## Step 0 — Freshness gate
Pick the newest `tp_snap_*.db`. If none exists, or the newest is > 8 days old, STOP: write a
one-line report noting stale/missing inputs and notify. Never evaluate stale data.

## Step 1 — DB analysis (the week's truth)
Open the snapshot with Python's sqlite3 (read-only). Compute at least:
- **Funnel:** counts by `action` and by `skip_reason_code` (decisions table), trailing 7 days
  and all-time.
- **Fills:** `trades` rows with `fill_status='filled'` in the last 7 days — count, and whether
  ANY exist. (Zero fills across the bot's life is the current baseline — flag loudly if it
  changes, in either direction.)
- **Cost:** `SUM(estimated_cost_usd)` and by-model from `token_usage`, last 7 days.
- **Health flags:** decisions with `reasoning LIKE '%max_tokens%'` (truncation, by model);
  any decision with a trade action but no matching `trades` row (recording gap); circuit-breaker
  state.
- Compare against last week's report in `knowledge/eval_reports/` if present.

## Step 2 — Hermes issues
Parse `hermes-issues.json`. For each issue:
- **Dedup** by title + body similarity — Hermes files near-duplicates (e.g. two identical macro
  coverage issues). Treat a cluster as ONE candidate.
- If it carries the **`do-not-auto-implement`** label → REPORT ONLY; never auto-ticket. For
  macro-date issues you MAY web-verify the proposed FOMC/CPI dates against the official Fed/BLS
  schedules and include the verified dates in the report for the operator to apply — still no
  auto-ticket.
- Otherwise it becomes a candidate for Step 3.

## Step 3 — Triage each candidate (noise vs. substantial; green vs. red)
For every candidate (DB findings AND Hermes issues):
1. **Ground in code.** Grep the repo to confirm the issue is real and NOT already fixed/committed
   (lesson: ticket 1001's fix was already in the code — always check current source before
   ticketing). If already handled, drop it.
2. **Noise filter.** Enough evidence? A pattern needs a real sample (≥ ~5 relevant decisions, or
   a clear code defect). A single odd data point is not a ticket.
3. **Scope classify** (per `tickets/README.md`):
   - **Green** → eligible for an auto-ticket.
   - **Red** (guardrails, circuit breaker, position sizing/caps, risk thresholds, execution/order
     logic, model changes, or anything that LOOSENS a safety rule) → REPORT ONLY. Recommend a
     human-authored ticket; never auto-ticket.

## Step 4 — Write tickets (green only)
For each surviving green candidate, write `tickets/ready/<NNNN>-<slug>.md` using the format +
`.ticket-counter` allocation from `tickets/README.md`. Each ticket must be self-contained, name
exact file paths (grep to find them), and carry a change-scoped `**Verify:**` command (a specific
`python -m pytest ...` invocation). **Cap ~3 tickets/week** — prioritize by evidence strength;
list deferred candidates in the report.

## Step 5 — Report + notify (the notification is the trigger — be explicit)
Write `knowledge/eval_reports/YYYY-MM-DD.md`: funnel numbers, fills status, cost, health flags,
what you ticketed (slugs), what you flagged red / report-only (with reasons), and what you
deliberately left alone.

Then hand off the notification. **IMPORTANT: this eval runs in a network-restricted sandbox that
CANNOT reach ntfy or SMTP — do NOT call `notify.py` yourself; it will fail.** Instead, WRITE the
notification to `C:\Users\davew\repos\trade-pilot\eval-inbox\eval-notify.json` (overwrite any
existing file) as a single JSON object:

```json
{ "title": "<one-line headline>", "message": "<one-line body>" }
```

A local scheduled job (`trade-pilot eval-notify`) on David's machine polls for that file, sends
the ntfy + email, and deletes it. The runner is **NOT automated** — David reads the push and, if
tickets were written, runs `/run-tickets` in Claude Code by hand. So the message MUST say plainly
whether action is needed:

- **Wrote N > 0 tickets:** title `weekly eval: <N> ticket(s) ready — run /run-tickets`; message
  lists the slugs and "open Claude Code and run /run-tickets".
- **Wrote zero tickets:** title `weekly eval: no tickets this week`; message a one-line why (e.g.
  "bot still not trading / all Hermes issues report-only / no real signal").

Name any red-scope / `do-not-auto-implement` items flagged for review in the message too, so David
knows the report has items awaiting his decision.

## Hard rules
- Ground every ticket in DB evidence or a Hermes issue AND current code. No speculative tickets.
- Never auto-ticket red-scope or `do-not-auto-implement` items.
- Dedup before ticketing. Cap ~3 tickets/week. "Nothing this week" is success.
- You only WRITE to `tickets/ready/` and `knowledge/eval_reports/`. You do NOT run git, deploy,
  or touch the bot's data/config. The nightly runner implements; the operator gates prod via PR.
