[CmdletBinding()]
param(
  [string]$ServerOrigin = 'http://192.168.1.100:8780',
  [ValidateRange(5, 86400)][int]$IntervalSeconds = 300,
  [switch]$Once
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FigmaToFGUI'
$sync = Join-Path $root 'Sync-Plugin.ps1'
$enabled = Join-Path $root 'updater.enabled'
$log = Join-Path $root 'updater.log'
if (-not (Test-Path -LiteralPath $sync -PathType Leaf)) { throw 'Sync-Plugin.ps1 is not installed' }

$sha256 = [Security.Cryptography.SHA256]::Create()
try {
  $rootHash = [BitConverter]::ToString($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($root))).Replace('-', '').Substring(0, 16)
} finally {
  $sha256.Dispose()
}
$createdNew = $false
$mutex = [Threading.Mutex]::new($true, "Local\FigmaToFGUIClientUpdater-$rootHash", [ref]$createdNew)
if (-not $createdNew) {
  $mutex.Dispose()
  exit 0
}

try {
  do {
    try {
      & $sync -ServerOrigin $ServerOrigin
      if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "Sync exited with code $LASTEXITCODE" }
    } catch {
      $line = "$(Get-Date -Format o) $($_.Exception.Message)`r`n"
      [IO.File]::AppendAllText($log, $line, [Text.UTF8Encoding]::new($false))
      if ($Once) { throw }
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSeconds
  } while (Test-Path -LiteralPath $enabled)
} finally {
  $mutex.ReleaseMutex()
  $mutex.Dispose()
}
