@echo off
REM Nightly headless runner for the trade-pilot self-improving loop.
REM Launched by Windows Task Scheduler. The machine must be awake (Task Scheduler
REM will not wake it). Claude Code needs valid stored credentials; if they expire,
REM the run fails silently (no notification) — treat a missing nightly ntfy as a
REM signal to re-check `claude` auth.
REM
REM Prereq: .claude\settings.local.json must contain {"defaultMode":"bypassPermissions"}
REM so the implementation subagents don't hang on permission prompts.
REM
REM If Task Scheduler can't find `claude`, replace it below with the full path
REM (find it in PowerShell with:  where claude).

cd /d "C:\Users\davew\repos\trade-pilot"
claude -p --dangerously-skip-permissions "/run-tickets" >> "eval-inbox\run-tickets-nightly.log" 2>&1
