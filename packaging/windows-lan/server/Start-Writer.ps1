$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\FigmaToFGUI'
$publicOrigin = (Get-Content -Raw (Join-Path $root 'public-origin.txt')).Trim()

# Windows PowerShell 5.1 wraps native stderr as NativeCommandError. Uvicorn
# writes normal startup information to stderr, so Stop would kill the server.
$ErrorActionPreference = 'Continue'
& (Join-Path $root 'venv\Scripts\python.exe') -m figma_to_fgui.cli serve `
  --production --lan `
  --data-dir (Join-Path $root 'data') `
  --rules (Join-Path $root 'rules\default\classification.yaml') `
  --public-origin $publicOrigin `
  --plugin-access-token-file (Join-Path $root 'plugin-access-token.txt') `
  --gateway-secret-file (Join-Path $root 'gateway-secret.txt') `
  --web-dist (Join-Path $root 'web-dist') `
  --plugin-manifest (Join-Path $root 'plugin\manifest.json') `
  --host 127.0.0.1 --port 8765 *>> (Join-Path $root 'logs\writer.log')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
