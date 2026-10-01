$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName 'FigmaToFGUI-ClientSync' -Confirm:$false -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'FigmaToFGUIClientSync' -ErrorAction SilentlyContinue
$root = Join-Path $env:LOCALAPPDATA 'FigmaToFGUI'
if (Test-Path $root) { Remove-Item -LiteralPath $root -Recurse -Force }
Write-Host 'Client sync removed. Remove the development plugin in Figma to finish uninstalling.'
