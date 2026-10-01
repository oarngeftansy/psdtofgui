[CmdletBinding()]
param(
  [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$packageDir = $PSScriptRoot
$repo = (Resolve-Path -LiteralPath (Join-Path $packageDir '..\..')).Path
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
  $OutputDirectory = Join-Path $repo '.local-release'
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
$staging = Join-Path $output 'PSD-FGUI视觉替换工具-联网测试版'
$zip = Join-Path $output 'PSD-FGUI视觉替换工具-联网测试版-20260927.zip'
$packageVersion = '2026.09.27.1'

function Copy-Tree([string]$Source, [string]$Destination) {
  [IO.Directory]::CreateDirectory($Destination) | Out-Null
  Get-ChildItem -LiteralPath $Source -Force | Copy-Item -Destination $Destination -Recurse -Force
}

Write-Host '正在构建应用界面...' -ForegroundColor Cyan
$pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $pnpm) { throw '打包电脑需要 pnpm。接收电脑不需要 Node.js 或 pnpm。' }
& $pnpm.Source --dir (Join-Path $repo 'apps\web-console') build
if ($LASTEXITCODE -ne 0) { throw '应用界面构建失败。' }

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
[IO.Directory]::CreateDirectory($staging) | Out-Null

Copy-Item -LiteralPath (Join-Path $packageDir '启动PSD替换工具.cmd') -Destination $staging
Copy-Item -LiteralPath (Join-Path $packageDir 'Start.ps1') -Destination $staging
Copy-Item -LiteralPath (Join-Path $packageDir '使用说明.txt') -Destination $staging
Copy-Item -LiteralPath (Join-Path $packageDir 'portable-constraints.txt') -Destination $staging
Copy-Tree (Join-Path $repo 'rules') (Join-Path $staging 'rules')
Copy-Tree (Join-Path $repo 'tests\fixtures') (Join-Path $staging 'fixtures')
Copy-Tree (Join-Path $repo 'apps\web-console\dist') (Join-Path $staging 'web')
[IO.File]::WriteAllText(
  (Join-Path $staging 'version.txt'),
  $packageVersion,
  [Text.UTF8Encoding]::new($false)
)

$py = Get-Command py.exe -ErrorAction SilentlyContinue
if ($py) {
  & $py.Source -3.11 -m pip wheel --disable-pip-version-check --no-deps --wheel-dir $staging $repo
} else {
  $python = Get-Command python.exe -ErrorAction SilentlyContinue
  if (-not $python) { throw '打包电脑需要 Python 3.11。' }
  & $python.Source -m pip wheel --disable-pip-version-check --no-deps --wheel-dir $staging $repo
}
if ($LASTEXITCODE -ne 0) { throw '核心程序 Wheel 构建失败。' }

if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath $staging -DestinationPath $zip -CompressionLevel Optimal
Write-Host "已生成：$zip" -ForegroundColor Green
