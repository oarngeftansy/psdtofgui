[CmdletBinding()]
param(
  [string]$PublicOrigin,
  [string]$TunnelToken,
  [string]$PluginId = '123456789'
)

$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw '请使用管理员 PowerShell 运行 Install.ps1'
}
if ([string]::IsNullOrWhiteSpace($PublicOrigin)) { $PublicOrigin = Read-Host '请输入公开 HTTPS 地址，例如 https://fgui-test.example.com' }
$origin = [Uri]$PublicOrigin
if ($origin.Scheme -ne 'https' -or -not [string]::IsNullOrEmpty($origin.PathAndQuery.Trim('/')) -or -not [string]::IsNullOrEmpty($origin.UserInfo)) {
  throw 'PublicOrigin 必须是一个不带路径、查询或账号信息的 HTTPS origin'
}
$PublicOrigin = $origin.GetLeftPart([UriPartial]::Authority)
if ($PluginId -notmatch '^\d+$') { throw 'PluginId 必须是数字' }
if ([string]::IsNullOrWhiteSpace($TunnelToken)) {
  $secure = Read-Host '请输入 Cloudflare Tunnel service token' -AsSecureString
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { $TunnelToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
if ([string]::IsNullOrWhiteSpace($TunnelToken)) { throw 'Tunnel token 不能为空' }

$bundle = $PSScriptRoot
$root = 'C:\ProgramData\FigmaToFGUI'
$directories = @($root, "$root\bin", "$root\data", "$root\logs", "$root\plugin", "$root\release", "$root\web-dist", "$root\rules")
foreach ($directory in $directories) { [IO.Directory]::CreateDirectory($directory) | Out-Null }

$pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
if ($pythonCommand) { & $pythonCommand.Source -3 -m venv "$root\venv" } else {
  $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
  if (-not $pythonCommand) { throw '未找到 Python 3.11+，请先安装 Python' }
  & $pythonCommand.Source -m venv "$root\venv"
}
$python = "$root\venv\Scripts\python.exe"
$pythonVersion = & $python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ([Version]$pythonVersion -lt [Version]'3.11') { throw "需要 Python 3.11+，当前为 $pythonVersion" }
& $python -m pip install --upgrade pip
$wheel = @(Get-ChildItem -LiteralPath "$bundle\runtime" -Filter 'figma_to_fgui_core-*.whl' -File)
if ($wheel.Count -ne 1) { throw '部署包必须包含且仅包含一个应用 wheel' }
& $python -m pip install "$($wheel[0].FullName)[server]"

function New-RandomSecret([string]$Path) {
  if (Test-Path -LiteralPath $Path -PathType Leaf) { return }
  [byte[]]$bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
  [IO.File]::WriteAllText($Path, [Convert]::ToBase64String($bytes), [Text.UTF8Encoding]::new($false))
}
New-RandomSecret "$root\plugin-access-token.txt"
New-RandomSecret "$root\gateway-secret.txt"
[IO.File]::WriteAllText("$root\public-origin.txt", $PublicOrigin, [Text.UTF8Encoding]::new($false))

Copy-Item -Path "$bundle\web-dist\*" -Destination "$root\web-dist" -Recurse -Force
Copy-Item -Path "$bundle\runtime\rules\*" -Destination "$root\rules" -Recurse -Force
Copy-Item -LiteralPath "$bundle\Caddyfile", "$bundle\Start-Writer.ps1", "$bundle\Start-Gateway.ps1" -Destination $root -Force

$pluginToken = (Get-Content -Raw "$root\plugin-access-token.txt").Trim()
$ui = Get-Content -Raw "$bundle\plugin-template\ui.html"
$oldOrigin = 'https://fgui.corp.example'
$oldToken = 'not-a-secret-public-test-placeholder-000000'
if (($ui.Split($oldOrigin).Count - 1) -ne 1 -or ($ui.Split($oldToken).Count - 1) -ne 1) { throw '插件模板配置占位符不唯一' }
$ui = $ui.Replace($oldOrigin, $PublicOrigin).Replace($oldToken, $pluginToken)
[IO.File]::WriteAllText("$root\plugin\ui.html", $ui, [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath "$bundle\plugin-template\code.js" -Destination "$root\plugin\code.js" -Force
$manifest = Get-Content -Raw "$bundle\plugin-template\manifest.json" | ConvertFrom-Json
$manifest.id = $PluginId
$manifest.networkAccess = [ordered]@{ allowedDomains = @($PublicOrigin) }
[IO.File]::WriteAllText("$root\plugin\manifest.json", ($manifest | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))

$caddy = "$root\bin\caddy.exe"
if (-not (Test-Path -LiteralPath $caddy)) {
  Invoke-WebRequest -UseBasicParsing 'https://caddyserver.com/api/download?os=windows&arch=amd64' -OutFile "$root\bin\caddy.zip"
  Expand-Archive -LiteralPath "$root\bin\caddy.zip" -DestinationPath "$root\bin\caddy-download" -Force
  Copy-Item -LiteralPath "$root\bin\caddy-download\caddy.exe" -Destination $caddy -Force
}
$cloudflared = "$root\bin\cloudflared.exe"
if (-not (Test-Path -LiteralPath $cloudflared)) {
  Invoke-WebRequest -UseBasicParsing 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $cloudflared
}

$writerAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\Start-Writer.ps1`"" -WorkingDirectory $root
$gatewayAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\Start-Gateway.ps1`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'FigmaToFGUI-Writer' -Action $writerAction -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Register-ScheduledTask -TaskName 'FigmaToFGUI-Gateway' -Action $gatewayAction -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

if (Get-Service cloudflared -ErrorAction SilentlyContinue) { throw '已存在 cloudflared 服务，请先确认并卸载旧 Tunnel 服务' }
& $cloudflared service install $TunnelToken
$TunnelToken = $null
Start-ScheduledTask -TaskName 'FigmaToFGUI-Writer'
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName 'FigmaToFGUI-Gateway'
Start-Sleep -Seconds 3

$installText = Get-Content -Raw "$bundle\PLUGIN-INSTALL.md"
[IO.File]::WriteAllText("$root\plugin\INSTALL.md", $installText.Replace('{{PUBLIC_ORIGIN}}', $PublicOrigin), [Text.UTF8Encoding]::new($false))
$pluginZip = "$root\release\Figma-to-FairyGUI-plugin.zip"
Compress-Archive -LiteralPath "$root\plugin\INSTALL.md", "$root\plugin\code.js", "$root\plugin\manifest.json", "$root\plugin\ui.html" -DestinationPath $pluginZip -Force
$hash = (Get-FileHash -Algorithm SHA256 $pluginZip).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$root\release\checksums.sha256", "$hash *Figma-to-FairyGUI-plugin.zip`n", [Text.UTF8Encoding]::new($false))

$localHealth = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8780/health' -TimeoutSec 15
if ($localHealth.StatusCode -ne 200) { throw '本地 Gateway 健康检查失败，请查看 logs 目录' }
Write-Host "安装完成。请验证 $PublicOrigin/health"
Write-Host "测试插件：$pluginZip"
Write-Host "SHA-256：$hash"
