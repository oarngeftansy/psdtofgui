[CmdletBinding()]
param(
  [string]$PluginDistDir,
  [string]$OutputDir
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($PluginDistDir)) {
  $PluginDistDir = Join-Path $PSScriptRoot "..\\..\\apps\\figma-plugin\\dist"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $OutputDir = Join-Path $PSScriptRoot "dist"
}

$requiredMembers = @("INSTALL.md", "code.js", "manifest.json", "ui.html")
$ordinalMembers = [string[]]$requiredMembers.Clone()
[Array]::Sort($ordinalMembers, [System.StringComparer]::Ordinal)
if (($requiredMembers -join "`0") -ne ($ordinalMembers -join "`0")) {
  throw "Package member names must be kept in ordinal order"
}

$sources = @{
  "INSTALL.md" = (Join-Path $PSScriptRoot "README.md")
  "code.js" = (Join-Path $PluginDistDir "code.js")
  "manifest.json" = (Join-Path $PluginDistDir "manifest.json")
  "ui.html" = (Join-Path $PluginDistDir "ui.html")
}
foreach ($memberName in $requiredMembers) {
  if (-not (Test-Path -LiteralPath $sources[$memberName] -PathType Leaf)) {
    throw "Required package file is missing: $($sources[$memberName])"
  }
}

[System.IO.Directory]::CreateDirectory($OutputDir) | Out-Null
$archivePath = Join-Path $OutputDir "Figma-to-FairyGUI-plugin.zip"
$checksumPath = Join-Path $OutputDir "checksums.sha256"
Remove-Item -LiteralPath $archivePath, $checksumPath -Force -ErrorAction SilentlyContinue

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$timestamp = [DateTimeOffset]::new(2000, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
$stream = [System.IO.File]::Open($archivePath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
  $archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
  try {
    foreach ($memberName in $requiredMembers) {
      $entry = $archive.CreateEntry($memberName, [System.IO.Compression.CompressionLevel]::Optimal)
      $entry.LastWriteTime = $timestamp
      $input = [System.IO.File]::OpenRead($sources[$memberName])
      try {
        $output = $entry.Open()
        try {
          $input.CopyTo($output)
        } finally {
          $output.Dispose()
        }
      } finally {
        $input.Dispose()
      }
    }
  } finally {
    $archive.Dispose()
  }
} finally {
  $stream.Dispose()
}

$hashStream = [System.IO.File]::OpenRead($archivePath)
try {
  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  try {
    $hash = ([System.BitConverter]::ToString($sha256.ComputeHash($hashStream))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha256.Dispose()
  }
} finally {
  $hashStream.Dispose()
}
[System.IO.File]::WriteAllText($checksumPath, "$hash *Figma-to-FairyGUI-plugin.zip`n", [System.Text.UTF8Encoding]::new($false))
