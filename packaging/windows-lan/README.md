# Windows 局域网同步包

服务端在 `192.168.1.100` 上以管理员 PowerShell 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\server\Install-Server.ps1
```

同事解压客户端包后，普通 PowerShell 运行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-Client.ps1
```

首次需在 Figma 中从 `%LOCALAPPDATA%\FigmaToFGUI\plugin\manifest.json` 导入开发插件。之后版本自动同步；Figma 运行时只下载不替换，关闭后自动安装。
