[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$OutputDirectory,
  [string[]]$FontFiles = @()
)

$ErrorActionPreference = 'Stop'
$packageDir = $PSScriptRoot
$repo = (Resolve-Path -LiteralPath (Join-Path $packageDir '..\..')).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
$bundleName = 'PSD-FGUI视觉替换工具-外发测试包-20260927'
$bundle = Join-Path $output $bundleName
$outerZip = Join-Path $output "$bundleName.zip"

[IO.Directory]::CreateDirectory($output) | Out-Null
$resolvedOutput = (Resolve-Path -LiteralPath $output).Path.TrimEnd('\')
foreach ($target in @($bundle, $outerZip)) {
  $parent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($target)).TrimEnd('\')
  if ($parent -ne $resolvedOutput) { throw "拒绝清理输出目录以外的路径：$target" }
}

& (Join-Path $packageDir 'build-package.ps1')
if ($LASTEXITCODE -ne 0) { throw '应用便携包构建失败。' }

if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Recurse -Force }
[IO.Directory]::CreateDirectory($bundle) | Out-Null
Copy-Item -LiteralPath (Join-Path $repo '.local-release\PSD-FGUI视觉替换工具-联网测试版') `
  -Destination (Join-Path $bundle '应用') -Recurse -Force

if ($FontFiles.Count -gt 0) {
  $fontDir = Join-Path $bundle '字体-请先安装'
  [IO.Directory]::CreateDirectory($fontDir) | Out-Null
  foreach ($font in $FontFiles) {
    $resolvedFont = (Resolve-Path -LiteralPath $font).Path
    Copy-Item -LiteralPath $resolvedFont -Destination $fontDir
  }
}

$note = @'
外发测试包使用顺序

1. 解压整个压缩包。
2. 安装“字体-请先安装”文件夹内的字体。
3. 进入“应用”文件夹，双击“启动PSD替换工具.cmd”。
4. 首次运行需要联网下载 Python 运行依赖；不需要安装 Node.js 或导入 Figma 插件。
5. 如需完成 FairyGUI 最终渲染和像素验证，接收电脑还需单独安装 FairyGUI Editor。

这是开发测试版。默认静态视觉替换已经在真实样本上跑通；多 Controller 页面、按钮状态和 Transition 中间帧的视觉映射仍需人工审核。
'@
[IO.File]::WriteAllText(
  (Join-Path $bundle '发包说明.txt'),
  $note,
  [Text.UTF8Encoding]::new($true)
)

if (Test-Path -LiteralPath $outerZip) { Remove-Item -LiteralPath $outerZip -Force }
Compress-Archive -LiteralPath $bundle -DestinationPath $outerZip -CompressionLevel Optimal
Write-Host "已生成：$outerZip" -ForegroundColor Green
