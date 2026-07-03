# Audits

Home for **recorded-journey visual/UX audits** of Elevate OTT — captured with the **Audit Capture Chrome extension** and reviewed by the `/audit` Cowork skill.

This folder is a **queue**. You capture a journey with the extension, it downloads a
`audit-<timestamp>.zip`, and the `/audit` skill ingests it, audits it, and files the
result here. Everything in this folder is gitignored except this README.

> **History:** this folder used to host a separate code-driven audit *harness*
> (`scripts/*-audit/`, `pnpm audit:blocks` / `audit:cms-ui`) and a legacy three-app capture
> stack (Auto Screen Capture + a transcript tool + DevTools Recorder). Both are retired. The
> Chrome extension replaced the three-app stack; the harness is no longer part of the
> workflow. The `scripts/*-audit/` code may still exist but is not used here.

---

## Layout

```
audits/
  README.md            ← this file (the only tracked thing in here)
  ready/               ← queue: packages waiting to be audited
    audit-<ts>/        ← one captured journey, unzipped + flattened
  done/                ← packages that have been audited
    audit-<ts>/        ← same package, now with audit-findings.md inside
```

- **State is the folder.** A package in `ready/` is queued; the same folder in `done/` has
  been audited. There is no manifest or status file — the folder it lives in *is* its status.
- **Order is the timestamp.** Package folders are named `audit-<YYYY-MM-DD_HH-MM-SS.mmm>`, so
  the oldest (lexically first) is next in the queue. FIFO, one at a time.
- **The zip travels with its package.** The original `audit-<ts>.zip` is kept inside its
  package folder as the archival source — if the unzipped frames ever go unreadable, the
  package can be re-extracted from it.

## A package folder contains

A flat folder (no sub-nesting) of whatever the extension captured:

- **Frames** — `YYYY-MM-DD_HH-MM-SS.mmm.jpeg`, viewport-only screenshots.
- **`*_annotated.png`** — frames you drew on (arrow/circle). High-signal.
- **`timeline.json`** — the authoritative join: every frame / click / MARK / console /
  network / annotation event on one shared clock.
- **`recording.json`** — the action spine (ordered clicks, routes, typed values). Read, never replayed.
- **`narration.txt`** — your MARKs + voice, each line tagged `[time] (/route)`.
- **`console.txt`** — captured `error`/`warn` console lines only.
- **`network-errors.txt`** — failed requests (4xx/5xx, blocked, aborted).
- **`environment.json`** — viewport / dpr / zoom / host / url context.
- **`audit-findings.md`** — the review output (added by `/audit`, present once it's in `done/`).
- **`audit-<ts>.zip`** — archived source.

## Workflow

1. **Capture** a journey with the Audit Capture extension (Start session → walk the flow →
   drop MARKs / annotations → Stop & export). It downloads `audit-<ts>.zip` to your Downloads
   folder.
2. **Run `/audit`** (no arguments). The skill:
   - audits the oldest package in `ready/` if any exist; otherwise
   - sweeps your **Downloads** folder for `audit-*.zip`, unzips + flattens each into `ready/`
     (moving the zip into its package folder), then audits the oldest;
   - if both are empty, prints the capture guide.
   - After a successful audit it writes `audit-findings.md` into the package and moves the
     folder from `ready/` to `done/`.
3. **Review** the findings; optionally turn them into tickets with `/create-tickets`.
4. **Clean up** occasionally with `/audit clean` (empties `done/`).

> **Downloads sweep requires Downloads to be mounted** in the Cowork session. Without it, the
> skill can't see Downloads — pass the zip path explicitly instead: `/audit "<path-to-zip>"`.

## Other ways to run it

- **`/audit "<path>"`** — audit a specific zip or folder directly, bypassing the queue.
- **`/audit clean`** — delete everything in `done/`.

See the `/audit` skill (`SKILL.md`) for the full capture guide and review logic.
