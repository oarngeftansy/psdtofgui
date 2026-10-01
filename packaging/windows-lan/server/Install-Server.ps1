[CmdletBinding()]
param(
  [string]$ServerAddress = '192.168.1.100',
  [int]$Port = 8780,
  [string]$PluginId = '123456789',
  [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$installingIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$installingUser = $installingIdentity.Name
$adminPrincipal = [Security.Principal.WindowsPrincipal]::new($installingIdentity)
if (-not $adminPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run Install-Server.ps1 from an administrator PowerShell' }
foreach ($service in @('Writer', 'Gateway')) {
  Stop-ScheduledTask -TaskName "FigmaToFGUI-$service" -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
$parsedAddress = $null
if (-not [Net.IPAddress]::TryParse($ServerAddress, [ref]$parsedAddress) -or $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'ServerAddress must be an IPv4 address' }
$bytes = $parsedAddress.GetAddressBytes()
$private = $bytes[0] -eq 10 -or ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
if (-not $private) { throw 'ServerAddress must be an RFC1918 private address' }
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid port' }
if ($PluginId -notmatch '^\d+$') { throw 'PluginId must be numeric' }
$origin = "http://${ServerAddress}:$Port"
$bundle = $PSScriptRoot
$root = 'C:\ProgramData\FigmaToFGUI'
foreach ($directory in @($root, "$root\bin", "$root\data", "$root\logs", "$root\plugin", "$root\release", "$root\web-dist", "$root\rules")) { [IO.Directory]::CreateDirectory($directory) | Out-Null }
foreach ($writableDirectory in @("$root\data", "$root\logs")) {
  & icacls.exe $writableDirectory /grant ($installingUser + ':(OI)(CI)M') /T /C | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not grant the service user access to $writableDirectory" }
}
Start-Transcript -Path "$root\install.log" -Append | Out-Null

$pythonExecutable = $null
$useLauncher = $false
if ($PythonPath) {
  if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'PythonPath must name an existing Python executable' }
  $pythonExecutable = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
  $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pythonCommand) { $pythonExecutable = $pythonCommand.Source; $useLauncher = $true }
  if (-not $pythonExecutable) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonExecutable = $pythonCommand.Source }
  }
  if (-not $pythonExecutable -or $pythonExecutable -like '*\WindowsApps\python.exe') { throw 'Python 3.11+ is required on the server; the Microsoft Store alias is not Python' }
}
if ($useLauncher) { & $pythonExecutable -3 -m venv "$root\venv" } else { & $pythonExecutable -m venv "$root\venv" }
$python = "$root\venv\Scripts\python.exe"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) { throw 'Python virtual environment creation failed' }
& $python -c 'import sys;raise SystemExit(sys.version_info < (3,11))'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required on the server' }
$wheel = @(Get-ChildItem -LiteralPath "$bundle\runtime" -Filter 'figma_to_fgui_core-*.whl' -File)
if ($wheel.Count -ne 1) { throw 'The package must contain exactly one application wheel' }
& $python -m pip install "$($wheel[0].FullName)[server]"
if ($LASTEXITCODE -ne 0) { throw 'Application dependency installation failed' }
& $python -m pip install --force-reinstall --no-deps $wheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'Application wheel installation failed' }

function New-RandomSecret([string]$Path) {
  if (Test-Path $Path) { return }
  [byte[]]$bytes = New-Object byte[] 32
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
  [IO.File]::WriteAllText($Path, [Convert]::ToBase64String($bytes), [Text.UTF8Encoding]::new($false))
}
function Get-Sha256Hex([string]$Path) {
  $stream = [IO.File]::OpenRead($Path)
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
  finally { $algorithm.Dispose(); $stream.Dispose() }
}
New-RandomSecret "$root\plugin-access-token.txt"
New-RandomSecret "$root\gateway-secret.txt"
[IO.File]::WriteAllText("$root\public-origin.txt", $origin, [Text.UTF8Encoding]::new($false))
Copy-Item -Path "$bundle\web-dist\*" -Destination "$root\web-dist" -Recurse -Force
Copy-Item -Path "$bundle\runtime\rules\*" -Destination "$root\rules" -Recurse -Force
Copy-Item -LiteralPath "$bundle\Start-Writer.ps1", "$bundle\Start-Gateway.ps1" -Destination $root -Force
$caddyConfig = (Get-Content -Raw "$bundle\Caddyfile").Replace(':8780 {', ":$Port {")
[IO.File]::WriteAllText("$root\Caddyfile", $caddyConfig, [Text.UTF8Encoding]::new($false))

$token = (Get-Content -Raw "$root\plugin-access-token.txt").Trim()
$ui = (Get-Content -Raw "$bundle\plugin-template\ui.html").Replace('https://fgui.corp.example', $origin).Replace('not-a-secret-public-test-placeholder-000000', $token)
[IO.File]::WriteAllText("$root\plugin\ui.html", $ui, [Text.UTF8Encoding]::new($false))
Copy-Item "$bundle\plugin-template\code.js" "$root\plugin\code.js" -Force
$manifest = Get-Content -Raw "$bundle\plugin-template\manifest.json" | ConvertFrom-Json
$manifest.id = $PluginId
$manifest.name = 'Figma to FairyGUI Create/Update (LAN)'
$manifest.networkAccess = [ordered]@{ allowedDomains = @('*'); reasoning = "Connects to the organization's private LAN Figma-to-FairyGUI service." }
[IO.File]::WriteAllText("$root\plugin\manifest.json", ($manifest | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))

$releaseId = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
$versionRoot = "$root\release\$releaseId"
[IO.Directory]::CreateDirectory($versionRoot) | Out-Null
$fileMap = [ordered]@{}
foreach ($name in @('manifest.json', 'code.js', 'ui.html')) {
  Copy-Item "$root\plugin\$name" "$versionRoot\$name" -Force
  $item = Get-Item "$versionRoot\$name"
  $fileMap[$name] = [ordered]@{ bytes = $item.Length; sha256 = Get-Sha256Hex $item.FullName }
}
$release = [ordered]@{ schemaVersion = 1; releaseId = $releaseId; serverOrigin = $origin; files = $fileMap }
[IO.File]::WriteAllText("$root\release\current.json.tmp", ($release | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
Move-Item "$root\release\current.json.tmp" "$root\release\current.json" -Force
$clientStage = "$root\release\client-$releaseId"
[IO.Directory]::CreateDirectory($clientStage) | Out-Null
Copy-Item -Path "$bundle\client\*" -Destination $clientStage -Recurse -Force
foreach ($clientScript in Get-ChildItem $clientStage -Filter '*.ps1' -File -Recurse) {
  $clientText = (Get-Content -Raw $clientScript.FullName).Replace('http://192.168.1.100:8780', $origin)
  [IO.File]::WriteAllText($clientScript.FullName, $clientText, [Text.UTF8Encoding]::new($false))
}
Compress-Archive -Path "$clientStage\*" -DestinationPath "$root\release\FigmaToFGUI-Client.zip" -Force

$caddy = "$root\bin\caddy.exe"
if (-not (Test-Path $caddy)) {
  $caddyDownload = "$root\bin\caddy.exe.download"
  $legacyDownload = "$root\bin\caddy.zip"
  $candidate = if (Test-Path -LiteralPath $legacyDownload) { $legacyDownload } else { $caddyDownload }
  if ($candidate -eq $caddyDownload) { Invoke-WebRequest -UseBasicParsing 'https://caddyserver.com/api/download?os=windows&arch=amd64' -OutFile $candidate }
  $stream = [IO.File]::OpenRead($candidate)
  try { $first = $stream.ReadByte(); $second = $stream.ReadByte() } finally { $stream.Dispose() }
  if ($first -ne 0x4D -or $second -ne 0x5A) { throw 'Downloaded Caddy file is not a Windows executable' }
  Move-Item -LiteralPath $candidate -Destination $caddy -Force
}
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $installingUser
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $installingUser -LogonType Interactive -RunLevel Highest
$cmd = "$env:SystemRoot\System32\cmd.exe"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
foreach ($service in @('Writer', 'Gateway')) {
  $scriptPath = "$root\Start-$service.ps1"
  $bootstrapLog = "$root\logs\$($service.ToLowerInvariant())-bootstrap.log"
  $arguments = "/d /c `"`"$powershell`" -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" >> `"$bootstrapLog`" 2>&1`""
  $action = New-ScheduledTaskAction -Execute $cmd -Argument $arguments -WorkingDirectory $root
  Register-ScheduledTask -TaskName "FigmaToFGUI-$service" -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Force | Out-Null
}
if (-not (Get-NetFirewallRule -DisplayName 'FigmaToFGUI LAN' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName 'FigmaToFGUI LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Domain,Private | Out-Null
}
Start-ScheduledTask 'FigmaToFGUI-Writer'; Start-Sleep 3
Start-ScheduledTask 'FigmaToFGUI-Gateway'; Start-Sleep 3
if ((Invoke-WebRequest -UseBasicParsing "$origin/health" -TimeoutSec 15).StatusCode -ne 200) { throw 'LAN service health check failed' }
Write-Host "Server installed: $origin"
Write-Host "Client package: $root\release\FigmaToFGUI-Client.zip"
