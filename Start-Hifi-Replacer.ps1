[CmdletBinding()]
param(
  [switch]$SkipInstall,
  [switch]$NoBrowser,
  [string]$DataDir = ""
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$commonGit = & git -C $repo rev-parse --path-format=absolute --git-common-dir 2>$null
$repositoryRoot = if ($LASTEXITCODE -eq 0 -and $commonGit) { Split-Path $commonGit -Parent } else { $repo }
$canonicalRun = Join-Path (Split-Path $repositoryRoot -Parent) 'local-run\current-source'
$localRoot = if (Test-Path -LiteralPath $canonicalRun -PathType Container) { $canonicalRun } else { Join-Path $repositoryRoot '.local-hifi-run' }
$venv = Join-Path $localRoot 'venv'
$data = if ($DataDir) { [IO.Path]::GetFullPath($DataDir) } else { Join-Path $localRoot 'data' }
$tokenFile = Join-Path $localRoot 'access-token.txt'
$python = Join-Path $venv 'Scripts\python.exe'

$appPort = 8766
$appUrl = "http://127.0.0.1:$appPort/"
# Keep the origin stable so browser session recovery survives restarts.
try {
  $bootstrap = Invoke-RestMethod -Uri ($appUrl + 'v1/local/bootstrap') -TimeoutSec 2
  if ((Test-Path -LiteralPath $tokenFile) -and $bootstrap.access_token -ceq ([IO.File]::ReadAllText($tokenFile).Trim()) -and -not $DataDir) {
    Write-Host "工具已在运行：$appUrl；材料目录：$data" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $appUrl }
    return
  }
} catch { }
$portProbe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $appPort)
try { $portProbe.Start() }
catch { throw "端口 $appPort 已由其他服务占用。请关闭冲突服务后重试；不会切换到另一套网址和会话。" }
finally { $portProbe.Stop() }

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

function Install-Prerequisite([string]$Id, [string]$Label) {
  $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $winget) {
    throw "缺少 $Label，且系统没有 winget。请先安装 $Label 后重新运行。"
  }
  Write-Host "正在安装 $Label..." -ForegroundColor Cyan
  & $winget.Source install --id $Id --exact --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "$Label 安装失败（退出码 $LASTEXITCODE）" }
  Refresh-ProcessPath
}

function Invoke-Pnpm([string[]]$Arguments) {
  $pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
  if ($pnpm) {
    & $pnpm.Source @Arguments
  } else {
    $corepack = Get-Command corepack.cmd -ErrorAction SilentlyContinue
    if ($corepack) {
      & $corepack.Source pnpm @Arguments
    } else {
      $npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
      if (-not $npx) { throw 'Node.js 已安装，但未找到 pnpm、corepack 或 npx' }
      & $npx.Source --yes pnpm@10 @Arguments
    }
  }
  if ($LASTEXITCODE -ne 0) { throw "pnpm 执行失败（退出码 $LASTEXITCODE）" }
}

function Test-PythonRuntime([string]$Candidate) {
  if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
  $previousErrorAction = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'SilentlyContinue'
    & $Candidate -c 'import aggdraw,fastapi,httpx,lxml,PIL,psd_tools,py7zr,pydantic,rarfile,skimage,typer,uvicorn,yaml,multipart,numpy' 2> $null | Out-Null
    $runtimeExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorAction
  }
  return $runtimeExitCode -eq 0
}

[IO.Directory]::CreateDirectory($localRoot) | Out-Null
[IO.Directory]::CreateDirectory($data) | Out-Null

$launcher = @(Resolve-PythonLauncher)
if ($launcher.Count -eq 0) {
  Install-Prerequisite 'Python.Python.3.11' 'Python 3.11'
  $launcher = @(Resolve-PythonLauncher)
}
if ($launcher.Count -eq 0) { throw 'Python 已安装，但当前终端仍无法找到它。请关闭窗口后重新双击启动脚本。' }

if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
  Install-Prerequisite 'OpenJS.NodeJS.LTS' 'Node.js LTS'
}
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
  throw 'Node.js 已安装，但当前终端仍无法找到它。请关闭窗口后重新双击启动脚本。'
}

$runtimeReady = Test-PythonRuntime $python
if (-not $runtimeReady) {
  $sharedCandidates = @(
    (Join-Path $repo '.venv\Scripts\python.exe'),
    (Join-Path $repo '..\..\.venv\Scripts\python.exe')
  )
  foreach ($candidate in $sharedCandidates) {
    if (Test-PythonRuntime $candidate) {
      $python = (Resolve-Path -LiteralPath $candidate).Path
      $runtimeReady = $true
      Write-Host "复用已安装的本地处理运行时：$python" -ForegroundColor Cyan
      break
    }
  }
}

if (-not $runtimeReady -and -not (Test-Path -LiteralPath $python -PathType Leaf)) {
  Write-Host '正在创建本地应用环境...' -ForegroundColor Cyan
  $launcherExe = $launcher[0]
  $launcherArgs = @($launcher | Select-Object -Skip 1) + @('-m', 'venv', $venv)
  & $launcherExe @launcherArgs
  if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}

if (-not $runtimeReady -and -not $SkipInstall) {
  Write-Host '正在安装/更新本地处理服务...' -ForegroundColor Cyan
  & $python -m pip install --disable-pip-version-check --no-cache-dir --no-compile -e "$repo[server]"
  if ($LASTEXITCODE -ne 0) { throw '本地处理服务安装失败' }
  $runtimeReady = Test-PythonRuntime $python
}

if (-not $runtimeReady) {
  throw '本地处理运行时缺少依赖。请取消 -SkipInstall 后重新运行。'
}

if (-not $SkipInstall) {
  Write-Host '正在安装本地界面依赖...' -ForegroundColor Cyan
  Invoke-Pnpm @('--dir', (Join-Path $repo 'apps\web-console'), 'install', '--frozen-lockfile')
}

if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
  [byte[]]$bytes = New-Object byte[] 32
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) }
  finally { $generator.Dispose() }
  [IO.File]::WriteAllText($tokenFile, [Convert]::ToBase64String($bytes), [Text.UTF8Encoding]::new($false))
}

Write-Host '正在构建 PSD 替换工具界面...' -ForegroundColor Cyan
Invoke-Pnpm @('--dir', (Join-Path $repo 'apps\web-console'), 'build')

Write-Host ''
Write-Host "PSD 替换工具正在 $appUrl 启动；关闭此窗口即可停止。" -ForegroundColor Green
if (-not $NoBrowser) {
  $openApp = "Start-Sleep -Seconds 2; Start-Process '$appUrl'"
  Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @('-NoProfile', '-Command', $openApp)
}

$env:PYTHONPATH = Join-Path $repo 'src'
$grantFile = Join-Path $data 'graph-conversion-grants.json'
if (Test-Path -LiteralPath $grantFile -PathType Leaf) {
  $env:HIFI_TYPE_CONVERSION_GRANTS_FILE = $grantFile
} else {
  Remove-Item Env:HIFI_TYPE_CONVERSION_GRANTS_FILE -ErrorAction SilentlyContinue
}
& $python -m figma_to_fgui.cli serve `
  --local-app `
  --data-dir $data `
  --rules (Join-Path $repo 'rules\default\classification.yaml') `
  --fixtures-root (Join-Path $repo 'tests\fixtures') `
  --web-dist (Join-Path $repo 'apps\web-console\dist') `
  --plugin-access-token-file $tokenFile `
  --host 127.0.0.1 `
  --port $appPort
if ($LASTEXITCODE -ne 0) { throw "本地服务启动失败（退出码 $LASTEXITCODE）" }
