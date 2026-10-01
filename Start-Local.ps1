[CmdletBinding()]
param(
  [switch]$PrepareOnly,
  [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$localRoot = Join-Path $repo '.local-run'
$venv = Join-Path $localRoot 'venv'
$plugin = Join-Path $localRoot 'plugin'
$data = Join-Path $localRoot 'data'
$tokenFile = Join-Path $localRoot 'plugin-access-token.txt'
$python = Join-Path $venv 'Scripts\python.exe'
$manifest = Join-Path $plugin 'manifest.json'

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

if ($repo -match '[^\x00-\x7F]') {
  throw '仓库路径必须只包含英文字符。请 clone 到 C:\src\figma-to-fgui 后重新运行。'
}

[IO.Directory]::CreateDirectory($localRoot) | Out-Null
[IO.Directory]::CreateDirectory($plugin) | Out-Null
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

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  Write-Host '正在创建 Python 本机环境...' -ForegroundColor Cyan
  $launcherExe = $launcher[0]
  $launcherArgs = @($launcher | Select-Object -Skip 1) + @('-m', 'venv', $venv)
  & $launcherExe @launcherArgs
  if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败' }
}

if (-not $SkipInstall) {
  Write-Host '正在安装/更新 Writer...' -ForegroundColor Cyan
  & $python -m pip install --disable-pip-version-check --upgrade pip
  if ($LASTEXITCODE -ne 0) { throw 'pip 更新失败' }
  & $python -m pip install --disable-pip-version-check -e "$repo[server]"
  if ($LASTEXITCODE -ne 0) { throw 'Writer 依赖安装失败' }

  Write-Host '正在安装前端依赖...' -ForegroundColor Cyan
  Invoke-Pnpm @('--dir', (Join-Path $repo 'apps\figma-plugin'), 'install', '--frozen-lockfile')
  Invoke-Pnpm @('--dir', (Join-Path $repo 'apps\web-console'), 'install', '--frozen-lockfile')
}

if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
  [byte[]]$bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
  [IO.File]::WriteAllText($tokenFile, [Convert]::ToBase64String($bytes), [Text.UTF8Encoding]::new($false))
}
$token = (Get-Content -Raw -LiteralPath $tokenFile).Trim()

Write-Host '正在构建本机管理界面和 Figma 插件...' -ForegroundColor Cyan
Invoke-Pnpm @('--dir', (Join-Path $repo 'apps\web-console'), 'build')
$env:FGUI_SERVER_ORIGIN = 'http://localhost:8765'
$env:FIGMA_PLUGIN_ID = '123456789'
$env:FGUI_PLUGIN_ACCESS_TOKEN = $token
$env:FGUI_PLUGIN_DIST_DIR = $plugin
Invoke-Pnpm @('--dir', (Join-Path $repo 'apps\figma-plugin'), 'build')
Remove-Item Env:FGUI_PLUGIN_ACCESS_TOKEN -ErrorAction SilentlyContinue

$parsedManifest = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
if ($parsedManifest.networkAccess.devAllowedDomains -notcontains 'http://localhost:8765') {
  throw '生成的插件没有绑定本机 Writer'
}

Write-Host ''
Write-Host '本机版已经准备好。' -ForegroundColor Green
Write-Host 'Figma 首次导入这个文件：' -ForegroundColor Yellow
Write-Host $manifest -ForegroundColor White
Write-Host '以后每次使用前，双击“启动本机版.cmd”并保持窗口开启。' -ForegroundColor Yellow

if ($PrepareOnly) { exit 0 }

Write-Host ''
Write-Host 'Writer 正在 http://localhost:8765 启动；按 Ctrl+C 可停止。' -ForegroundColor Green
$env:PYTHONPATH = Join-Path $repo 'src'
& $python -m figma_to_fgui.cli serve `
  --local-app `
  --data-dir $data `
  --rules (Join-Path $repo 'rules\default\classification.yaml') `
  --fixtures-root (Join-Path $repo 'tests\fixtures') `
  --web-dist (Join-Path $repo 'apps\web-console\dist') `
  --plugin-access-token-file $tokenFile `
  --host 127.0.0.1 `
  --port 8765
