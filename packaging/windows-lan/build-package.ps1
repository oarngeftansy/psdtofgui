[CmdletBinding()]
param([string]$OutputDir = '.local-acceptance\windows-lan-package', [string]$WheelDir = '.local-acceptance\windows-lan-wheel')
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$output = [IO.Path]::GetFullPath((Join-Path $repo $OutputDir))
if (-not $output.StartsWith([IO.Path]::GetFullPath((Join-Path $repo '.local-acceptance')), [StringComparison]::OrdinalIgnoreCase)) { throw 'OutputDir must be inside .local-acceptance' }
$stage = Join-Path $output 'stage'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
[IO.Directory]::CreateDirectory((Join-Path $stage 'server')) | Out-Null
Copy-Item "$PSScriptRoot\server\*" (Join-Path $stage 'server') -Recurse
Copy-Item "$PSScriptRoot\client" (Join-Path $stage 'server\client') -Recurse
[IO.Directory]::CreateDirectory((Join-Path $stage 'server\runtime')) | Out-Null
$wheels = @(Get-ChildItem ([IO.Path]::GetFullPath((Join-Path $repo $WheelDir))) -Filter 'figma_to_fgui_core-*.whl' -File)
if ($wheels.Count -ne 1) { throw 'WheelDir must contain exactly one wheel' }
Copy-Item $wheels[0].FullName (Join-Path $stage 'server\runtime')
Copy-Item (Join-Path $repo 'rules') (Join-Path $stage 'server\runtime') -Recurse
Copy-Item (Join-Path $repo 'apps\web-console\dist') (Join-Path $stage 'server\web-dist') -Recurse
Copy-Item (Join-Path $repo 'apps\figma-plugin\dist') (Join-Path $stage 'server\plugin-template') -Recurse
Copy-Item "$PSScriptRoot\client" (Join-Path $stage 'client') -Recurse
$zip = Join-Path $output 'FigmaToFGUI-Windows-LAN.zip'
$clientZip = Join-Path $output 'FigmaToFGUI-Client.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
if (Test-Path $clientZip) { Remove-Item $clientZip -Force }
Compress-Archive (Join-Path $stage '*') $zip -CompressionLevel Optimal
Compress-Archive (Join-Path $stage 'client\*') $clientZip -CompressionLevel Optimal
$checksums = @(
  "$((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()) *$([IO.Path]::GetFileName($zip))",
  "$((Get-FileHash $clientZip -Algorithm SHA256).Hash.ToLowerInvariant()) *$([IO.Path]::GetFileName($clientZip))"
)
$checksums | Set-Content (Join-Path $output 'checksums.sha256') -Encoding ascii
Write-Output $zip
Write-Output $clientZip
