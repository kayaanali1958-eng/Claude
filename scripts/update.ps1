# Safe one-command update for the desk (Windows). Keeps your journal, state, lessons and keys.
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1
$desk = Split-Path -Parent $PSScriptRoot
Set-Location $desk
$keep = 'desk_state.json','journal.md','lessons.md','crypto_state.json','crypto_journal.md','crypto_live_state.json','crypto_live_journal.md'
$bak = Join-Path $desk 'news\update_backup'
New-Item -ItemType Directory $bak -Force | Out-Null
foreach ($f in $keep + 'settings.md') { if (Test-Path $f) { Copy-Item $f $bak -Force } }
git fetch origin claude/robinhood-trading-mcp-0z7yb0
git reset --hard origin/claude/robinhood-trading-mcp-0z7yb0
foreach ($f in $keep) { if (Test-Path "$bak\$f") { Copy-Item "$bak\$f" $f -Force } }
# Keep your MODE and CRYPTO_MODE switches from the old settings.md
if (Test-Path "$bak\settings.md") {
  $old = Get-Content "$bak\settings.md"
  $new = Get-Content settings.md
  foreach ($key in 'MODE','CRYPTO_MODE') {
    $line = $old | Where-Object { $_ -match "^$key`: " } | Select-Object -First 1
    if ($line) { $new = $new | ForEach-Object { if ($_ -match "^$key`: ") { $line } else { $_ } } }
  }
  $new | Set-Content settings.md
}
pip install -q yfinance pandas tabulate websockets
Write-Host 'Updated. Your journal, state, lessons, .env and MODE / CRYPTO_MODE switches were kept.'
Select-String '^(MODE|CRYPTO_MODE): ' settings.md
