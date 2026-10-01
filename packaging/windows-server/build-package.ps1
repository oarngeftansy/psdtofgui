[CmdletBinding()]
param(
  [string]$OutputDir = '.local-acceptance\windows-server-package',
  [string]$WheelDir = '.local-acceptance\windows-server-wheel'
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$output = [IO.Path]::GetFullPath((Join-Path $repo $OutputDir))
if (-not $output.StartsWith([IO.Path]::GetFullPath((Join-Path $repo '.local-acceptance')), [StringComparison]::OrdinalIgnoreCase)) {
  throw 'OutputDir 必须位于项目 .local-acceptance 目录内'
}
$stage = Join-Path $output 'stage'
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
[IO.Directory]::CreateDirectory($stage) | Out-Null
Copy-Item -LiteralPath "$PSScriptRoot\Install.ps1", "$PSScriptRoot\Start-Writer.ps1", "$PSScriptRoot\Start-Gateway.ps1", "$PSScriptRoot\Caddyfile", "$PSScriptRoot\README.md", "$PSScriptRoot\PLUGIN-INSTALL.md" -Destination $stage
$runtime = Join-Path $stage 'runtime'
[IO.Directory]::CreateDirectory($runtime) | Out-Null
$wheelRoot = [IO.Path]::GetFullPath((Join-Path $repo $WheelDir))
$wheels = @(Get-ChildItem -LiteralPath $wheelRoot -Filter 'figma_to_fgui_core-*.whl' -File)
if ($wheels.Count -ne 1) { throw 'WheelDir 必须包含且仅包含一个 figma_to_fgui_core wheel' }
Copy-Item -LiteralPath $wheels[0].FullName -Destination $runtime
Copy-Item -LiteralPath (Join-Path $repo 'rules') -Destination $runtime -Recurse
Copy-Item -LiteralPath (Join-Path $repo 'apps\web-console\dist') -Destination (Join-Path $stage 'web-dist') -Recurse
Copy-Item -LiteralPath (Join-Path $repo 'apps\figma-plugin\dist') -Destination (Join-Path $stage 'plugin-template') -Recurse
$zip = Join-Path $output 'Figma-to-FairyGUI-Windows-Server.zip'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $output 'checksums.sha256'), "$hash *Figma-to-FairyGUI-Windows-Server.zip`n", [Text.UTF8Encoding]::new($false))
Write-Output $zip
