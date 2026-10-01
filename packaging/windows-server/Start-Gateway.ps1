$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\FigmaToFGUI'
$env:FGUI_GATEWAY_SECRET = (Get-Content -Raw (Join-Path $root 'gateway-secret.txt')).Trim()
& (Join-Path $root 'bin\caddy.exe') run --config (Join-Path $root 'Caddyfile') --adapter caddyfile *>> (Join-Path $root 'logs\gateway.log')
