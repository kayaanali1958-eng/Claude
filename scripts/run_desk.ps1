# One desk-manager run (Windows). Task Scheduler calls this every 5 minutes; it exits early
# outside Mon-Fri 9:00 AM-4:05 PM ET.
$DeskDir = Split-Path -Parent $PSScriptRoot
Set-Location $DeskDir
$et = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Eastern Standard Time')
if ($et.DayOfWeek -in 'Saturday','Sunday') { exit 0 }
$hm = [int]$et.ToString('HHmm')
if ($hm -lt 900 -or $hm -gt 1605) { exit 0 }
if (Test-Path "$DeskDir\STOP") { exit 0 }   # kill switch: create a file named STOP
# Save the Claude plan's usage (crypto live orders need it too): run every 15 minutes
# (:00, :15, :30, :45), plus the 15:55 flatten. The timer fires every 5 minutes.
$m = $et.Minute % 15
if ($m -ge 5 -and -not ($hm -ge 1555 -and $hm -le 1559)) { exit 0 }
# After a "usage limit" error, pause for an hour instead of failing every run.
$limit = "$DeskDir\news\usage_limit"
if ((Test-Path $limit) -and (Get-Item $limit).LastWriteTime -gt (Get-Date).AddMinutes(-60)) { exit 0 }

# Skip if the previous run is still going (stale after 20 minutes).
$lock = "$DeskDir\.desk.lock"
if (Test-Path $lock) {
  if ((Get-Item $lock).LastWriteTime -gt (Get-Date).AddMinutes(-20)) { exit 0 }
  Remove-Item $lock -Recurse -Force
}
New-Item -ItemType Directory $lock | Out-Null
try {
  # Order-placing tools are granted only when settings.md says exactly "MODE: live".
  $read  = 'get_accounts get_portfolio get_equity_positions get_equity_orders get_equity_quotes get_equity_historicals get_index_quotes get_index_historicals get_earnings_calendar get_politician_trades get_equity_fundamentals get_earnings_results get_equity_analyst_ratings get_scans get_scanner_filter_specs get_scanner_datapoints preview_scan run_scan review_equity_order'.Split(' ')
  $write = 'place_equity_order cancel_equity_order'.Split(' ')
  $rh = $read
  if (Select-String -Path settings.md -Pattern '^MODE: live$' -Quiet) { $rh = $read + $write }
  $allowed = @('Read','Write','Edit','Task','Agent','WebSearch','WebFetch')
  foreach ($p in 'mcp__robinhood-trading__','mcp__RobinHood__','mcp__claude_ai_RobinHood__') { foreach ($t in $rh) { $allowed += "$p$t" } }

  New-Item -ItemType Directory logs -Force | Out-Null
  New-Item -ItemType Directory news -Force | Out-Null
  # Keep the live news listener running (no-op if already running or no keys in .env).
  $pidFile = 'news\listener.pid'
  $running = (Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)
  if ((Test-Path '.env') -and -not $running) {
    $p = Start-Process python -ArgumentList 'scripts\news_listener.py' -WindowStyle Hidden -PassThru -RedirectStandardOutput 'logs\news_listener.log' -RedirectStandardError 'logs\news_listener.err'
    $p.Id | Set-Content $pidFile
  }
  if (Test-Path 'news\live.jsonl') { Get-Content 'news\live.jsonl' -Tail 200 | Set-Content 'news\latest.jsonl' }
  # Every weekday after the close: rebuild the playbook with today's data before the review.
  if ($hm -ge 1600 -and -not (Test-Path "backtests\report_$(Get-Date -Format yyyy-MM-dd).md")) {
    & python scripts\backtest.py SPY QQQ NVDA AAPL MSFT AMZN META TSLA AMD GOOGL *>> logs\backtest.log
  }
  $log = "logs\desk-$($et.ToString('yyyy-MM-dd')).log"
  $now = $et.ToString('yyyy-MM-dd HH:mm') + ' ET (' + $et.DayOfWeek + ')'
  "===== run $now =====" | Add-Content $log
  & claude -p "Desk run. Current time: $now. Follow CLAUDE.md exactly for one run, then stop." --allowedTools @allowed --permission-mode dontAsk *>> $log
  $rc = $LASTEXITCODE
  "===== exit $rc =====" | Add-Content $log
  if ($rc -ne 0 -and (Get-Content $log -Tail 5 | Select-String -Pattern 'session limit|usage limit|rate limit' -Quiet)) { New-Item -ItemType File $limit -Force | Out-Null }
  & python scripts\notify.py $rc *>> logs\notify.log
} finally { Remove-Item $lock -Recurse -Force -ErrorAction SilentlyContinue }
