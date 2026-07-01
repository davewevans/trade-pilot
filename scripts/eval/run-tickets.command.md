---
allowed-tools: Agent, Bash, Read, Grep, Glob
description: Nightly autonomous runner for the trade-pilot self-improving loop. Implements ready tickets on `development`, change-scoped pytest gate, auto-push, ntfy+email notify. Headless-safe.
---

<!--
  INSTALL: copy this file to .claude/commands/run-tickets.md in the repo root so Claude Code
  registers it as the /run-tickets slash command:
      copy scripts\eval\run-tickets.command.md .claude\commands\run-tickets.md
  (Cowork can't write into .claude/ directly — it's a protected path — hence the copy.)
-->

# trade-pilot ticket runner (autonomous, nightly)

You are the **orchestrator** for the trade-pilot ticket backlog. Implement every autonomous
ticket in `tickets/ready/`, one at a time, on the `development` branch, gate each with its own
change-scoped test command, push `development` when done, and send one summary notification.
This runs **unattended** (headless `claude -p`) — never wait for user input. If a ticket is
genuinely ambiguous or unsafe, skip it and report it in the final notification.

## Contract (hold these)

1. **You own ALL git + the gate. Implementation subagents only edit files.** The gate command
   is the only success signal, and *you* run it. Tell every subagent: do NOT run git, do NOT
   commit/branch/push, do NOT move the ticket file.
2. **Serial only.** One ticket at a time, in sorted filename order (numeric prefix = order).
3. **Base branch is `development`.** Every ticket branches off `development` and merges back into
   it. `main` is NEVER touched — David opens the PR `development → main` himself. Never
   `git push --force`; only `development` is ever pushed.
4. **Honor `CLAUDE.md`.** Especially the per-account `ContextBuilder` rule and the append-only
   persistence contracts. Never weaken a guardrail or circuit-breaker rule beyond exactly what a
   ticket specifies. Tickets are pre-vetted green-scope by the weekly Cowork eval, but stay in
   scope regardless.
5. **This is paper-trading infra.** Auto-push to `development` is expected; the human gate is the
   PR into `main`, not this run.

## Procedure

### 1. Preflight
- `git rev-parse --verify development` — confirm the base branch exists.
- If the working tree has **tracked** modifications/staged changes, STOP and notify
  ("working tree dirty — runner refused"). Untracked gitignored files (`eval-inbox/`,
  `tp_snap*.db`) are fine.
- `git checkout development && git pull --ff-only` so you build on the latest.

### 2. Select tickets
Scan `tickets/ready/*.md` (skip `README.md`), sorted filename order. Read each header; **skip**
and note the reason when:
- `**Status:**` is `done` / `in-progress`.
- not `**Autonomous:** yes` (report "ungraded/attended — left for the weekly eval").
- a `failed/<slug>` branch already exists (report "prior failure — delete branch to retry").
Print the selected list.

### 3. Run loop (serial)
For each selected `tickets/ready/<slug>.md`:
1. `git checkout development` → `git branch -D ticket/<slug>` (nuke stale) →
   `git checkout -b ticket/<slug>`.
2. **Implement**: launch ONE general-purpose subagent with the prompt below; wait for it.
3. **Gate (change-scoped)**: run the ticket's `**Verify:**` command in the FOREGROUND, wrapped
   in `timeout 600`, output to a log file; read pass/fail from the exit code. If the ticket has
   no `**Verify:**` line, treat as fail — a green-scope ticket must ship a real change-scoped
   gate; do NOT pass on "no tests".
4. **Retry once** on failure: fresh subagent with the failure log, then re-run the gate.
5. **Resolve**:
   - **Green** → write `tickets/done/<slug>.md` (synthesis shape below); `git rm`
     `tickets/ready/<slug>.md`; stage the ticket's own files + the done file; commit
     `feat(ticket): <slug>`; `git checkout development`;
     `git merge --no-ff -m "merge(ticket): <slug>" ticket/<slug>`; `git branch -d ticket/<slug>`.
   - **Red** (still failing after the retry) → commit `attempt(failed): <slug>`; rename the branch
     to `failed/<slug>`; `git checkout development`; leave `tickets/ready/<slug>.md` in place;
     record it for the summary.

### 4. Push + notify
- If any ticket merged: `git push origin development`.
- Send ONE summary:
  `python scripts/eval/notify.py "trade-pilot: <N> merged, <M> failed" "<merged slugs> | failed: <slug: reason> | development pushed"`
- If nothing merged and nothing failed, still notify ("run-tickets: no ready tickets") so a
  quiet night is distinguishable from a broken cron.

## Done-synthesis shape (`tickets/done/<slug>.md`, ~150–300 words)
```
# <Title> (`<slug>`)
**Status:** done
**Synthesized:** <YYYY-MM-DD>
## Purpose
## What was done
## Key files
## Notes for future work
```

## Implementation subagent prompt (per ticket, fresh context)
> You are implementing a single ticket for the trade-pilot codebase (Python).
> Read `tickets/ready/<slug>.md` in full, then read every file it references before changing
> anything. Implement exactly what the ticket specifies — nothing more; do not refactor unrelated
> code or add docs unless the ticket requires it. Follow CLAUDE.md, especially the per-account
> ContextBuilder rule and the append-only persistence contracts. Do NOT run git — no commit,
> branch, push, or moving the ticket file; the orchestrator owns all of that. When done, list the
> files you changed with a one-line summary each.

## Notes
- The gate is the ticket's own `**Verify:**` command — change-scoped on purpose, so a pre-existing
  unrelated failure (e.g. the `turnover_wheel_idle` prompt fixture) can never sink a good ticket.
- Env required for notify: `NTFY_TOPIC` and `EVAL_NOTIFY_EMAIL` (notify.py loads `.env`).
- Headless: never prompt the user. Ambiguity or risk → skip the ticket, report it, move on.
