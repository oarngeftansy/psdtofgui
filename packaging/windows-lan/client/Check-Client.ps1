[CmdletBinding()]
param([string]$ServerOrigin = 'http://192.168.1.100:8780')
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FigmaToFGUI'
try {
  $health = Invoke-RestMethod -UseBasicParsing "$ServerOrigin/health" -TimeoutSec 10
  $release = Invoke-RestMethod -UseBasicParsing "$ServerOrigin/client/releases/current.json" -TimeoutSec 10
  $installedFile = Join-Path $root 'installed-release.txt'
  $installed = if (Test-Path $installedFile) { (Get-Content -Raw $installedFile).Trim() } else { 'not-installed' }
  Write-Host "Server: $ServerOrigin ($($health.status))"
  Write-Host "Published release: $($release.releaseId)"
  Write-Host "Installed release: $installed"
  Write-Host "Figma running: $([bool](Get-Process -Name Figma -ErrorAction SilentlyContinue))"
} catch {
  Write-Error "Client diagnostic failed for $ServerOrigin : $($_.Exception.Message)"
  exit 1
}
