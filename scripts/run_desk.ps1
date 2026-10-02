# One desk-manager run (Windows). Task Scheduler calls this every 5 minutes; it exits early
# outside Mon-Fri 9:00 AM-4:05 PM ET.
$DeskDir = Split-Path -Parent $PSScriptRoot
Set-Location $DeskDir
$et = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Eastern Standard Time')
if ($et.DayOfWeek -in 'Saturday','Sunday') { exit 0 }
$hm = [int]$et.ToString('HHmm')
if ($hm -lt 900 -or $hm -gt 1605) { exit 0 }
if (Test-Path "$DeskDir\STOP") { exit 0 }   # kill switch: create a file named STOP

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
  $log = "logs\desk-$($et.ToString('yyyy-MM-dd')).log"
  $now = $et.ToString('yyyy-MM-dd HH:mm') + ' ET (' + $et.DayOfWeek + ')'
  "===== run $now =====" | Add-Content $log
  & claude -p "Desk run. Current time: $now. Follow CLAUDE.md exactly for one run, then stop." --allowedTools @allowed --permission-mode dontAsk *>> $log
  "===== exit $LASTEXITCODE =====" | Add-Content $log
} finally { Remove-Item $lock -Recurse -Force -ErrorAction SilentlyContinue }
