# Weekly self-improving loop — inputs

`pull-eval-inputs.ps1` refreshes everything the weekly Cowork evaluation reads, into
`eval-inbox/` (gitignored):

- `tp_snap_YYYY-MM-DD.db` — consistent snapshot of the live Render DB (`/data/trade_pilot.db`).
- `hermes-issues.json` — open Hermes suggestions from `davewevans/trade-pilot` issues.

## Setup (one-time)

1. **SSH to Render** — key added to your Render account; host is the trade-pilot web
   service (`srv-d7dumdrbc2fs73e8up40@ssh.virginia.render.com`). First manual connect
   accepts the host key.
2. **GitHub read access** — the repo is on your **personal** account, so either:
   - `gh auth login` (or `gh auth switch`) so the default account can read
     `davewevans/trade-pilot`, or
   - create a fine-grained PAT (resource owner = personal, repo = trade-pilot,
     **Issues: Read**, **Metadata: Read**) and set `GH_TOKEN` for the scheduled task.
   The secret stays on this machine; it is never committed and never handed to Cowork.
3. **Schedule** — Windows Task Scheduler, weekly (e.g. Fri 21:00):
   `powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\davew\repos\trade-pilot\scripts\eval\pull-eval-inputs.ps1"`
   The machine must be awake at that time.

## The loop

```
Fri 21:00  pull-eval-inputs.ps1        -> eval-inbox/ (snapshot + issues)   [Task Scheduler, no AI]
Fri 22:00  Cowork weekly evaluation    -> reads snapshot + issues + repo code,
                                           writes green-scope tickets to tickets/ready/   [Cowork scheduled task]
nightly    Claude Code run-tickets     -> implements ready tickets on `development`,
                                           pytest gate, push, notify                       [Task Scheduler + headless claude]
you        review PR development -> main                                                   [human gate]
```

Red-scope changes (guardrails, position caps, risk thresholds, anything that *loosens*
a safety rule) are never auto-ticketed — the eval flags them in its report for a
human-authored ticket only.
