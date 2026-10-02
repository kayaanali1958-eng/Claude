# Installs the 5-minute desk timer on Windows (Task Scheduler).
$runner = Join-Path $PSScriptRoot 'run_desk.ps1'
schtasks /Create /F /TN ClaudeTradingDesk /SC MINUTE /MO 5 `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$crypto = Join-Path $PSScriptRoot 'crypto_desk.py'
$desk = Split-Path -Parent $PSScriptRoot
schtasks /Create /F /TN ClaudeCryptoDesk /SC MINUTE /MO 5 /ST 00:00 `
  /TR "cmd /c cd /d `"$desk`" && (if not exist logs mkdir logs) && python `"$crypto`" >> logs\crypto_desk.log 2>&1"
Write-Host 'Installed. Turn off with:  schtasks /Delete /TN ClaudeTradingDesk /F'
Write-Host 'Crypto desk (every 5 min: positions; on the hour: new trades; 24/7). Turn off with:  schtasks /Delete /TN ClaudeCryptoDesk /F'
