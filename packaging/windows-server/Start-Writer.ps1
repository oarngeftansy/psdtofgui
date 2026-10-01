$ErrorActionPreference = 'Stop'
$root = 'C:\ProgramData\FigmaToFGUI'
$python = Join-Path $root 'venv\Scripts\python.exe'
& $python -m figma_to_fgui.cli serve `
  --production `
  --data-dir (Join-Path $root 'data') `
  --rules (Join-Path $root 'rules\default\classification.yaml') `
  --public-origin (Get-Content -Raw (Join-Path $root 'public-origin.txt')).Trim() `
  --plugin-access-token-file (Join-Path $root 'plugin-access-token.txt') `
  --gateway-secret-file (Join-Path $root 'gateway-secret.txt') `
  --web-dist (Join-Path $root 'web-dist') `
  --plugin-manifest (Join-Path $root 'plugin\manifest.json') `
  --host 127.0.0.1 `
  --port 8765 *>> (Join-Path $root 'logs\writer.log')
