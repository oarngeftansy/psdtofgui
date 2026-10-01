[CmdletBinding()]
param(
  [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$runtimeRoot = Join-Path $appRoot '.runtime'
$venv = Join-Path $runtimeRoot 'venv'
$data = Join-Path $appRoot 'data'
$tokenFile = Join-Path $runtimeRoot 'access-token.txt'
$python = Join-Path $venv 'Scripts\python.exe'
$versionFile = Join-Path $appRoot 'version.txt'
$installedVersionFile = Join-Path $runtimeRoot 'installed-version.txt'
$constraintsFile = Join-Path $appRoot 'portable-constraints.txt'

function Get-AvailableLocalPort {
  foreach ($candidate in 8765..8785) {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $candidate)
    try {
      $listener.Start()
      return $candidate
    } catch [Net.Sockets.SocketException] {
      continue
    } finally {
      $listener.Stop()
    }
  }
  throw '8765–8785 端口均被占用，请关闭旧的 PSD 替换工具后重试。'
}

function Refresh-ProcessPath {
  $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = "$machinePath;$userPath"
}

function Resolve-PythonLauncher {
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) { return @($py.Source, '-3.11') }
  $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($candidate -and $candidate.Source -notlike '*\WindowsApps\python.exe') {
    return @($candidate.Source)
  }
  return @()
}

function Install-Python {
  $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $winget) {
    throw '缺少 Python 3.11，且系统没有 winget。请先安装 Python 3.11 后重新运行。'
  }
  Write-Host '正在安装 Python 3.11...' -ForegroundColor Cyan
  & $winget.Source install --id Python.Python.3.11 --exact --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "Python 3.11 安装失败（退出码 $LASTEXITCODE）" }
  Refresh-ProcessPath
}

function Test-Runtime([string]$Candidate) {
  if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
  $previousErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    & $Candidate -c 'import aggdraw,fastapi,figma_to_fgui,httpx,lxml,PIL,psd_tools,pydantic,skimage,typer,uvicorn,yaml,multipart,numpy' 2> $null | Out-Null
    $runtimeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  return $runtimeExitCode -eq 0
}

[IO.Directory]::CreateDirectory($runtimeRoot) | Out-Null
[IO.Directory]::CreateDirectory($data) | Out-Null

$launcher = @(Resolve-PythonLauncher)
if ($launcher.Count -eq 0) {
  Install-Python
  $launcher = @(Resolve-PythonLauncher)
}
if ($launcher.Count -eq 0) {
  throw 'Python 已安装，但当前窗口仍无法找到它。请关闭窗口后重新双击启动脚本。'
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  Write-Host '正在创建本地运行环境...' -ForegroundColor Cyan
  $launcherExe = $launcher[0]
  $launcherArgs = @($launcher | Select-Object -Skip 1) + @('-m', 'venv', $venv)
  & $launcherExe @launcherArgs
  if ($LASTEXITCODE -ne 0) { throw '创建 Python 运行环境失败' }
}

$packageVersion = (Get-Content -LiteralPath $versionFile -Raw).Trim()
$installedVersion = if (Test-Path -LiteralPath $installedVersionFile -PathType Leaf) {
  (Get-Content -LiteralPath $installedVersionFile -Raw).Trim()
} else {
  ''
}
$wheel = Get-ChildItem -LiteralPath $appRoot -Filter 'figma_to_fgui_core-*.whl' -File |
  Select-Object -First 1
if ($null -eq $wheel) { throw '应用包缺少核心程序文件，请重新获取完整压缩包。' }

if (-not (Test-Runtime $python) -or $installedVersion -ne $packageVersion) {
  Write-Host '首次启动，正在联网安装本地处理组件...' -ForegroundColor Cyan
  & $python -m pip install --disable-pip-version-check --no-cache-dir --no-compile `
    --constraint $constraintsFile "$($wheel.FullName)[server]"
  if ($LASTEXITCODE -ne 0) { throw '本地处理组件安装失败，请检查网络后重试。' }
  [IO.File]::WriteAllText(
    $installedVersionFile,
    $packageVersion,
    [Text.UTF8Encoding]::new($false)
  )
}
if (-not (Test-Runtime $python)) { throw '本地处理组件不完整，请删除 .runtime 文件夹后重试。' }

if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
  [byte[]]$bytes = New-Object byte[] 32
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) }
  finally { $generator.Dispose() }
  [IO.File]::WriteAllText($tokenFile, [Convert]::ToBase64String($bytes), [Text.UTF8Encoding]::new($false))
}

$appPort = Get-AvailableLocalPort
$appUrl = "http://localhost:$appPort"
Write-Host ''
Write-Host "PSD 替换工具正在 $appUrl 启动；关闭此窗口即可停止。" -ForegroundColor Green

if (-not $NoBrowser) {
  $openApp = "Start-Sleep -Seconds 2; Start-Process '$appUrl'"
  Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $openApp)
}

& $python -m figma_to_fgui.cli serve `
  --local-app `
  --data-dir $data `
  --rules (Join-Path $appRoot 'rules\default\classification.yaml') `
  --fixtures-root (Join-Path $appRoot 'fixtures') `
  --web-dist (Join-Path $appRoot 'web') `
  --plugin-access-token-file $tokenFile `
  --host 127.0.0.1 `
  --port $appPort
if ($LASTEXITCODE -ne 0) { throw "本地服务启动失败（退出码 $LASTEXITCODE）" }
