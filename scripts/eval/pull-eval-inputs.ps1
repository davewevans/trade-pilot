<#
  pull-eval-inputs.ps1
  Friday-night job. Refreshes the inputs the weekly Cowork evaluation reads:
    1. Pull a consistent (WAL-safe) snapshot of the live Render trade-pilot DB.
    2. Export open Hermes GitHub issues (davewevans/trade-pilot) to JSON.
    3. Prune DB snapshots older than 10 days.
  Everything lands in eval-inbox/ (gitignored). NO secrets live in this file.

  Prereqs
    - OpenSSH client (ssh/scp) on PATH; the Render SSH key added to your account.
    - gh CLI authed to the account that owns davewevans/trade-pilot
      (personal, not elevate-ott), OR set $env:GH_TOKEN to a fine-grained PAT
      scoped to trade-pilot with Issues:read + Metadata:read.

  Schedule
    - Windows Task Scheduler, e.g. weekly Fri 21:00. The machine must be awake.
    - The Cowork weekly eval runs ~1h later and reads eval-inbox/.

  Notes
    - VACUUM INTO (not a raw copy) is required: the DB is WAL mode, so a plain
      cp/scp of the live file is a torn read.
    - The VACUUM SQL is piped to sqlite3 over stdin on purpose — embedding it in
      the ssh command line hits PowerShell's native-arg quoting and silently
      produced an empty snapshot in testing. stdin sidesteps all of that.
#>

$ErrorActionPreference = 'Stop'

$Repo      = 'C:\Users\davew\repos\trade-pilot'
$Inbox     = Join-Path $Repo 'eval-inbox'
$SshHost   = 'srv-d7dumdrbc2fs73e8up40@ssh.virginia.render.com'
$DbPath    = '/data/trade_pilot.db'
$Date      = Get-Date -Format 'yyyy-MM-dd'
$SnapName  = "tp_snap_$Date.db"
$LocalSnap = Join-Path $Inbox $SnapName

New-Item -ItemType Directory -Force -Path $Inbox | Out-Null

# 1) Snapshot on the instance, copy down, clean up the remote temp file.
#    Native ssh/scp failures do NOT throw in PowerShell, so check $LASTEXITCODE.
#    Render SSH cold-connects can time out, so retry. Delete any stale same-day
#    file first so a leftover snapshot can't fake success.
if (Test-Path $LocalSnap) { Remove-Item $LocalSnap -Force }

$snapOk = $false
foreach ($attempt in 1..3) {
    "VACUUM INTO '/tmp/$SnapName';" | ssh -o UpdateHostKeys=no -o ConnectTimeout=30 $SshHost "sqlite3 $DbPath"
    if ($LASTEXITCODE -eq 0) {
        scp -s -o UpdateHostKeys=no "${SshHost}:/tmp/$SnapName" "$LocalSnap"
        if ($LASTEXITCODE -eq 0 -and (Test-Path $LocalSnap) -and (Get-Item $LocalSnap).Length -gt 0) {
            $snapOk = $true; break
        }
    }
    Write-Warning "snapshot attempt $attempt/3 failed; retrying in 10s..."
    Start-Sleep -Seconds 10
}
ssh -o UpdateHostKeys=no $SshHost "rm -f /tmp/$SnapName" 2>$null | Out-Null
if (-not $snapOk) { throw "Snapshot FAILED after 3 attempts — is Render SSH reachable?" }
Write-Host "snapshot ok: $LocalSnap ($([math]::Round((Get-Item $LocalSnap).Length/1MB,1)) MB)"

# 2) Export open Hermes issues. Best-effort: a gh hiccup must not lose the snapshot.
#    NOTE: gh's nonzero exit is NOT a PowerShell terminating error, so we must
#    check $LASTEXITCODE explicitly — otherwise a failed export writes a junk file
#    and still reports success.
try {
    $issuesPath = Join-Path $Inbox 'hermes-issues.json'
    $issuesJson = gh issue list --repo davewevans/trade-pilot --state open --limit 200 `
        --json number,title,body,labels,createdAt,url 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh exited $LASTEXITCODE : $issuesJson" }
    ($issuesJson -join "`n") | Set-Content -Path $issuesPath -Encoding utf8
    $count = @((Get-Content $issuesPath -Raw | ConvertFrom-Json)).Count  # authoritative count from the file
    Write-Host "issues ok: $issuesPath ($count open)"
} catch {
    Write-Warning "gh issue export FAILED (snapshot still saved): $($_.Exception.Message)"
}

# 3) Prune DB snapshots older than 10 days (issues file is overwritten each run).
Get-ChildItem $Inbox -Filter 'tp_snap_*.db' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-10) } |
    Remove-Item -Force

Write-Host "eval inputs refreshed for $Date"
