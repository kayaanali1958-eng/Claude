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
# Laptops: run on battery too, wake the PC from sleep to run, and catch up on a missed run.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -WakeToRun
foreach ($t in 'ClaudeTradingDesk','ClaudeCryptoDesk') { Set-ScheduledTask -TaskName $t -Settings $settings | Out-Null }
Write-Host 'Tasks set to run on battery, wake the PC from sleep, and catch up after a missed run.'
