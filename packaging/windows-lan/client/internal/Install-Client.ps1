[CmdletBinding()]
param([string]$ServerOrigin = 'http://192.168.1.100:8780')
$ErrorActionPreference = 'Stop'
$clientRoot = Split-Path -Parent $PSScriptRoot
$root = Join-Path $env:LOCALAPPDATA 'FigmaToFGUI'
[IO.Directory]::CreateDirectory($root) | Out-Null
Copy-Item -LiteralPath (Join-Path $clientRoot 'Sync-Plugin.ps1') -Destination (Join-Path $root 'Sync-Plugin.ps1') -Force
Copy-Item -LiteralPath (Join-Path $clientRoot 'Client-Updater.ps1') -Destination (Join-Path $root 'Client-Updater.ps1') -Force
[IO.File]::WriteAllText((Join-Path $root 'server-origin.txt'), $ServerOrigin, [Text.UTF8Encoding]::new($false))
& (Join-Path $root 'Sync-Plugin.ps1') -ServerOrigin $ServerOrigin -Force

$escapedRoot = $root.Replace('''', '''''')
$escapedOrigin = $ServerOrigin.Replace('''', '''''')
$command = "& '$escapedRoot\Client-Updater.ps1' -ServerOrigin '$escapedOrigin'"
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runValue = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$command`""
Set-ItemProperty -Path $runKey -Name 'FigmaToFGUIClientSync' -Value $runValue
[IO.File]::WriteAllText((Join-Path $root 'updater.enabled'), 'enabled', [Text.UTF8Encoding]::new($false))
Unregister-ScheduledTask -TaskName 'FigmaToFGUI-ClientSync' -Confirm:$false -ErrorAction SilentlyContinue
$updater = Join-Path $root 'Client-Updater.ps1'
Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$updater`" -ServerOrigin `"$ServerOrigin`""

$manifest = Join-Path $root 'plugin\manifest.json'
Write-Host "Installed. Import this manifest once in Figma development plugins: $manifest"
Start-Process explorer.exe -ArgumentList "/select,`"$manifest`""
