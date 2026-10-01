$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\FigmaToFGUI'
$env:FGUI_GATEWAY_SECRET = (Get-Content -Raw (Join-Path $root 'gateway-secret.txt')).Trim()

# Caddy writes ordinary info logs to stderr. Windows PowerShell 5.1 exposes
# that stream as NativeCommandError, which must not terminate the gateway.
$ErrorActionPreference = 'Continue'
& (Join-Path $root 'bin\caddy.exe') run --config (Join-Path $root 'Caddyfile') --adapter caddyfile *>> (Join-Path $root 'logs\gateway.log')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
