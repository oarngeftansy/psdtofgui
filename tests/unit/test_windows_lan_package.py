import hashlib
import json
import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packaging" / "windows-lan"


def test_lan_package_has_one_time_client_install_and_atomic_sync() -> None:
    installer = (PACKAGE / "client" / "internal" / "Install-Client.ps1").read_text("utf-8")
    launcher = (PACKAGE / "client" / "Install-Client.cmd").read_text("utf-8")
    diagnostics = (PACKAGE / "client" / "Check-Client.ps1").read_text("utf-8")
    sync = (PACKAGE / "client" / "Sync-Plugin.ps1").read_text("utf-8")
    updater = (PACKAGE / "client" / "Client-Updater.ps1").read_text("utf-8")

    assert "Client-Updater.ps1" in installer
    assert "Sync-Plugin.ps1') -ServerOrigin $ServerOrigin -Force" in installer
    assert "Install-Client.ps1" in launcher and "ExecutionPolicy Bypass" in launcher
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" in installer
    assert "Register-ScheduledTask" not in installer
    assert "Scheduled sync was unavailable" not in installer
    assert "Start-Process" in installer
    assert "SHA256" in updater
    assert "Local\\FigmaToFGUIClientUpdater-" in updater
    assert "Start-Sleep" in updater
    assert "[switch]$Once" in updater
    assert "/health" in diagnostics and "installed-release.txt" in diagnostics
    assert "manifest.json" in installer
    assert "current.json" in sync
    assert "SHA256" in sync
    assert "staging" in sync
    assert "backup" in sync
    assert "Get-Process" in sync and "Figma" in sync


def test_client_launcher_keeps_success_and_failure_results_visible() -> None:
    launcher = (PACKAGE / "client" / "Install-Client.cmd").read_text("utf-8")

    assert "internal\\Install-Client.ps1" in launcher
    assert not (PACKAGE / "client" / "Install-Client.ps1").exists()
    assert (PACKAGE / "client" / "internal" / "Install-Client.ps1").is_file()
    assert "Installation completed successfully." in launcher
    assert "Installation failed" in launcher
    assert launcher.lower().count("pause") >= 2


def test_lan_server_install_limits_firewall_and_publishes_versioned_release() -> None:
    installer = (PACKAGE / "server" / "Install-Server.ps1").read_text("utf-8")
    gateway = (PACKAGE / "server" / "Caddyfile").read_text("utf-8")

    assert "LocalSubnet" in installer
    assert "192.168.1.100" in installer
    assert "current.json" in installer
    assert "SHA256" in installer
    assert "[string]$PythonPath" in installer
    assert "WindowsApps" in installer
    assert "venv\\Scripts\\python.exe" in installer
    assert "RandomNumberGenerator]::Create()" in installer
    assert "RandomNumberGenerator]::Fill" not in installer
    assert "Start-Transcript" in installer
    assert "pip install --upgrade pip" not in installer
    assert "pip install --force-reinstall --no-deps" in installer
    assert "Stop-ScheduledTask" in installer
    assert "Get-ChildItem $clientStage -Filter '*.ps1' -File -Recurse" in installer
    assert "$manifest.name = 'Figma to FairyGUI Create/Update (LAN)'" in installer
    assert "caddy.exe.download" in installer
    assert "Expand-Archive" not in installer
    assert "New-ScheduledTaskTrigger -AtLogOn -User $installingUser" in installer
    assert "New-ScheduledTaskPrincipal -UserId $installingUser -LogonType Interactive" in installer
    assert "icacls.exe $writableDirectory" in installer
    assert "System32\\cmd.exe" in installer
    assert "allowedDomains = @('*')" in installer
    assert "-User 'SYSTEM'" not in installer
    assert "-AtStartup" not in installer
    writer = (PACKAGE / "server" / "Start-Writer.ps1").read_text("utf-8")
    gateway_script = (PACKAGE / "server" / "Start-Gateway.ps1").read_text("utf-8")
    assert "Windows PowerShell 5.1" in writer
    assert "Windows PowerShell 5.1" in gateway_script
    assert "$ErrorActionPreference = 'Continue'" in writer
    assert "$ErrorActionPreference = 'Continue'" in gateway_script
    assert "client/releases" in gateway
    assert "FigmaToFGUI-Client.zip" in (PACKAGE / "build-package.ps1").read_text("utf-8")
    assert ".Replace('http://192.168.1.100:8780', $origin)" in installer


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell runtime")
def test_client_sync_repairs_a_corrupted_installed_release(tmp_path: Path) -> None:
    release_id = "202609030001"
    server_root = tmp_path / "server" / "client" / "releases"
    version_root = server_root / release_id
    version_root.mkdir(parents=True)
    origin_holder: dict[str, str] = {}
    payloads = {
        "code.js": b"plugin code",
        "ui.html": b"<html>plugin</html>",
    }
    for name, payload in payloads.items():
        (version_root / name).write_bytes(payload)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path / "server"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    origin_holder["origin"] = f"http://127.0.0.1:{server.server_port}"
    manifest = json.dumps(
        {
            "id": "123456789",
            "networkAccess": {
                "allowedDomains": ["*"],
                "reasoning": "Connects to the organization's private LAN Figma-to-FairyGUI service.",
            },
        },
        separators=(",", ":"),
    ).encode()
    (version_root / "manifest.json").write_bytes(manifest)
    files = {}
    for path in version_root.iterdir():
        payload = path.read_bytes()
        files[path.name] = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    (server_root / "current.json").write_text(
        json.dumps({"schemaVersion": 1, "releaseId": release_id, "files": files}), "utf-8"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path / "client")
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(PACKAGE / "client" / "Sync-Plugin.ps1"), "-ServerOrigin", origin_holder["origin"],
    ]
    fake_figma = tmp_path / "Figma.exe"
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", fake_figma)
    figma_process = subprocess.Popen(
        [str(fake_figma), "/d", "/c", "ping -n 30 127.0.0.1 >nul"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        first = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
        assert first.returncode == 0, first.stdout + first.stderr
        installed = tmp_path / "client" / "FigmaToFGUI" / "plugin" / "code.js"
        assert installed.exists(), "A first install must complete even while Figma is running"
        installed.write_bytes(b"corrupted")
        second = subprocess.run(command + ["-Force"], env=environment, capture_output=True, text=True, check=False)
        assert second.returncode == 0, second.stdout + second.stderr
        assert installed.read_bytes() == payloads["code.js"]
    finally:
        figma_process.terminate()
        figma_process.wait(timeout=5)
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell runtime")
def test_background_updater_can_complete_a_real_sync_once(tmp_path: Path) -> None:
    release_id = "202609040002"
    server_root = tmp_path / "server" / "client" / "releases"
    version_root = server_root / release_id
    version_root.mkdir(parents=True)
    payloads = {"code.js": b"code", "ui.html": b"<html></html>"}
    for name, payload in payloads.items():
        (version_root / name).write_bytes(payload)
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path / "server"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    (version_root / "manifest.json").write_text(
        json.dumps({
            "id": "123456789",
            "networkAccess": {
                "allowedDomains": ["*"],
                "reasoning": "Connects to the organization's private LAN Figma-to-FairyGUI service.",
            },
        }),
        "utf-8",
    )
    files = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in version_root.iterdir()
    }
    (server_root / "current.json").write_text(
        json.dumps({"schemaVersion": 1, "releaseId": release_id, "files": files}), "utf-8"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path / "client")
    client_root = tmp_path / "client" / "FigmaToFGUI"
    client_root.mkdir(parents=True)
    shutil.copy2(PACKAGE / "client" / "Sync-Plugin.ps1", client_root / "Sync-Plugin.ps1")
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(PACKAGE / "client" / "Client-Updater.ps1"), "-ServerOrigin", origin, "-Once",
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        active_manifest = client_root / "plugin" / "manifest.json"
        staged_manifest = client_root / "staging" / release_id / "manifest.json"
        assert active_manifest.exists() or staged_manifest.exists()
        if staged_manifest.exists():
            assert (client_root / "pending-release.txt").read_text("utf-8") == release_id
        assert (client_root / "installed-release.txt").read_text("utf-8") == release_id
    finally:
        server.shutdown()
        server.server_close()
