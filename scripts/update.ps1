# Safe one-command update for the desk (Windows). Keeps your journal, state, lessons and keys.
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1
$desk = Split-Path -Parent $PSScriptRoot
Set-Location $desk
$keep = 'desk_state.json','journal.md','lessons.md','crypto_state.json','crypto_journal.md','crypto_live_state.json','crypto_live_journal.md'
$bak = Join-Path $desk 'news\update_backup'
New-Item -ItemType Directory $bak -Force | Out-Null
foreach ($f in $keep) { if (Test-Path $f) { Copy-Item $f $bak -Force } }
git fetch origin claude/robinhood-trading-mcp-0z7yb0
git reset --hard origin/claude/robinhood-trading-mcp-0z7yb0
foreach ($f in $keep) { if (Test-Path "$bak\$f") { Copy-Item "$bak\$f" $f -Force } }
pip install -q yfinance pandas tabulate websockets
Write-Host 'Updated. Your journal, state, lessons and .env were kept.'
