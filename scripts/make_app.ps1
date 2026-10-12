# Puts a "Trading Desk" icon on the Windows desktop that opens the dashboard in its own app window
# (Microsoft Edge app mode: no tabs or address bar). Run once:
#   powershell -ExecutionPolicy Bypass -File scripts\make_app.ps1
$desk = Split-Path -Parent $PSScriptRoot
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { Write-Host 'Microsoft Edge not found; open http://localhost:8765 in any browser instead.'; exit 1 }

# Make sure the dashboard is running (the desk also starts it every 5 minutes).
$up = $false
try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8765); $up = $true; $c.Close() } catch {}
if (-not $up) { Start-Process python -ArgumentList "`"$desk\scripts\dashboard.py`"" -WorkingDirectory $desk -WindowStyle Hidden }

$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Trading Desk.lnk'
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $edge
$s.Arguments = '--app=http://localhost:8765 --window-size=480,900'
$s.IconLocation = "$env:SystemRoot\System32\imageres.dll,177"
$s.Description = 'Trading desk dashboard'
$s.Save()
Write-Host "Created '$lnk'. Double-click it to open the desk like an app (right-click > Pin to taskbar to keep it handy)."
