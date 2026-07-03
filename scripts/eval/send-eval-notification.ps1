<#
  send-eval-notification.ps1  (runs LOCALLY on David's machine)

  The weekly Cowork eval runs in a network-restricted sandbox that can't reach ntfy or
  SMTP, so it writes its notification to eval-inbox\eval-notify.json instead. This script
  runs on the local machine (which CAN reach ntfy/SMTP), sends it via notify.py, and
  deletes the file so it fires exactly once.

  Scheduled to poll every ~30 min across the Friday-evening / Saturday window (Task
  Scheduler), so it catches the file whether the eval ran on time or on catch-up. When
  there's no pending file it exits immediately (near-zero cost).
#>
$ErrorActionPreference = 'Stop'
$Repo = 'C:\Users\davew\repos\trade-pilot'
$File = Join-Path $Repo 'eval-inbox\eval-notify.json'

if (-not (Test-Path $File)) { exit 0 }   # nothing pending — normal case

try {
    $n = Get-Content $File -Raw | ConvertFrom-Json
    $title   = if ($n.title)   { [string]$n.title }   else { 'trade-pilot weekly eval' }
    $message = if ($n.message) { [string]$n.message } else { '(no message)' }
    # notify.py loads .env from the cwd; the .cmd wrapper cd's into the repo first.
    python (Join-Path $Repo 'scripts\eval\notify.py') $title $message
    Remove-Item $File -Force   # fire once
    Write-Host "eval notification sent and cleared."
} catch {
    Write-Warning "send-eval-notification failed (leaving file for retry): $($_.Exception.Message)"
}
