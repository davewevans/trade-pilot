# Ticket system (`tickets/`)

Repo-native tickets for the trade-pilot self-improving loop. One markdown file per ticket,
no external tool. Mirrors the pattern from the ott-cms-frontend project — **minus ClickUp**.

## The loop

```
Fri 21:00  scripts/eval/pull-eval-inputs.ps1   -> eval-inbox/ (DB snapshot + hermes-issues.json)   [Task Scheduler, no AI]
Fri 22:00  Cowork weekly evaluation            -> reads snapshot + Hermes issues + repo code,
                                                  writes green-scope tickets to tickets/ready/       [Cowork scheduled task]
nightly    Claude Code /run-tickets            -> implements ready/ on `development`, change-scoped
                                                  pytest gate, push, notify (ntfy + email)           [Task Scheduler + headless claude]
you        review PR development -> main                                                             [the only human gate]
```

## Folders

- `ready/`    — autonomous tickets the runner implements (`**Autonomous:** yes`).
- `attended/` — needs a human in the loop; the runner skips these.
- `done/`     — compact synthesis written after a ticket merges.
- `.ticket-counter` — global, ever-incrementing next number (4-digit; lexical sort == numeric).

## Ticket format — `ready/<NNNN>-<slug>.md`

```markdown
# <Title>

**Status:** not started
**Autonomous:** yes
**Verify:** <change-scoped test command, e.g. python -m pytest tests/test_foo.py -q>

## Goal
<what this changes and why>

## Key files
- `path/to/file.py` — role

## Implementation steps
1. ...

## Constraints
<repo rules that apply; the ContextBuilder-per-account rule, etc.>

## Verification
<exact commands proving the change; mirror **Verify:**>
```

## Scope rules — the loop's safety rails (READ BEFORE WRITING A TICKET)

- **Green (auto-ticketable):** prompt wording/context, logging/observability, report & eval
  fixes, bounded bug fixes (e.g. the cost-panel `token_usage` repoint), non-risk config —
  and everything ships **toggleable** (feature flag / env), default off.
- **Red (NEVER auto — the eval flags in its report only, for a human-authored ticket):**
  `strategies/guardrails.py`, `strategies/circuit_breaker.py`, position sizing / caps, any
  risk threshold, execution/order logic, model changes, and anything that *loosens* a safety
  rule.
- Hermes issues labeled **`do-not-auto-implement`** are report-only — never auto-ticketed.
- Dedup Hermes issues by title+content before ticketing (Hermes files near-duplicates).
- **"No ticket this week" is a valid, common outcome.** There is no quota. Do not manufacture
  tickets to look useful — the whole loop's failure mode is inventing busywork.
