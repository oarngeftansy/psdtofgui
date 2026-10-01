[CmdletBinding()]
param([string]$ServerOrigin = 'http://192.168.1.100:8780', [switch]$Force)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FigmaToFGUI'
$plugin = Join-Path $root 'plugin'
$stagingRoot = Join-Path $root 'staging'
$backupRoot = Join-Path $root 'backup'
$stateFile = Join-Path $root 'installed-release.txt'
[IO.Directory]::CreateDirectory($stagingRoot) | Out-Null
[IO.Directory]::CreateDirectory($backupRoot) | Out-Null

function Get-Sha256Hex([string]$Path) {
  $stream = [IO.File]::OpenRead($Path)
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
  finally { $algorithm.Dispose(); $stream.Dispose() }
}

$release = Invoke-RestMethod -UseBasicParsing "$ServerOrigin/client/releases/current.json" -TimeoutSec 20
if ($release.schemaVersion -ne 1 -or $release.releaseId -notmatch '^[A-Za-z0-9._-]{1,80}$') { throw 'Invalid server release manifest' }
$names = @($release.files.PSObject.Properties.Name)
if ((@($names | Sort-Object) -join ',') -ne 'code.js,manifest.json,ui.html') { throw 'Invalid server release file list' }
$installedIsCurrent = (Test-Path $stateFile) -and ((Get-Content -Raw $stateFile).Trim() -eq $release.releaseId)
if ($installedIsCurrent) {
  foreach ($name in $names) {
    $installedFile = Join-Path $plugin $name
    if (-not (Test-Path $installedFile) -or (Get-Item $installedFile).Length -ne $release.files.$name.bytes -or (Get-Sha256Hex $installedFile) -ne $release.files.$name.sha256) {
      $installedIsCurrent = $false
      break
    }
  }
}
if ($installedIsCurrent) { exit 0 }

$stage = Join-Path $stagingRoot $release.releaseId
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
[IO.Directory]::CreateDirectory($stage) | Out-Null
foreach ($name in $names) {
  $meta = $release.files.$name
  if ($meta.sha256 -notmatch '^[a-f0-9]{64}$' -or $meta.bytes -lt 1 -or $meta.bytes -gt 52428800) { throw "Invalid release file metadata: $name" }
  $target = Join-Path $stage $name
  Invoke-WebRequest -UseBasicParsing "$ServerOrigin/client/releases/$($release.releaseId)/$name" -OutFile $target -TimeoutSec 60
  if ((Get-Item $target).Length -ne $meta.bytes) { throw "Release file size mismatch: $name" }
  if ((Get-Sha256Hex $target) -ne $meta.sha256) { throw "Release file hash mismatch: $name" }
}
$manifest = Get-Content -Raw (Join-Path $stage 'manifest.json') | ConvertFrom-Json
$networkAccess = $manifest.networkAccess
if (
  $networkAccess.allowedDomains.Count -ne 1 -or
  $networkAccess.allowedDomains[0] -ne '*' -or
  [string]::IsNullOrWhiteSpace([string]$networkAccess.reasoning)
) { throw 'LAN plugin manifest must use Figma-compatible wildcard network access with a reason' }

# Updating files already loaded by a running Figma instance must wait, but a
# first install has no loaded plugin files and must finish immediately so the
# user receives an importable manifest.
if (-not $Force -and (Test-Path $plugin) -and (Get-Process -Name Figma -ErrorAction SilentlyContinue)) {
  [IO.File]::WriteAllText((Join-Path $root 'pending-release.txt'), $release.releaseId, [Text.UTF8Encoding]::new($false))
  Write-Output 'Figma is running. Update staged until Figma exits.'
  exit 0
}

$backup = Join-Path $backupRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))
try {
  if (Test-Path $plugin) { Move-Item -LiteralPath $plugin -Destination $backup }
  Move-Item -LiteralPath $stage -Destination $plugin
  [IO.File]::WriteAllText($stateFile, $release.releaseId, [Text.UTF8Encoding]::new($false))
  Remove-Item -LiteralPath (Join-Path $root 'pending-release.txt') -Force -ErrorAction SilentlyContinue
} catch {
  if (Test-Path $plugin) { Remove-Item -LiteralPath $plugin -Recurse -Force }
  if (Test-Path $backup) { Move-Item -LiteralPath $backup -Destination $plugin }
  throw
}
