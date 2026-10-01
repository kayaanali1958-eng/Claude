# Installs the 5-minute desk timer on Windows (Task Scheduler).
$runner = Join-Path $PSScriptRoot 'run_desk.ps1'
schtasks /Create /F /TN ClaudeTradingDesk /SC MINUTE /MO 5 `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$runner`""
Write-Host 'Installed. Turn off with:  schtasks /Delete /TN ClaudeTradingDesk /F'
