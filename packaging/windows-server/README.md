# Windows 测试服务器部署包

此包把 Writer 部署到另一台 Windows，并通过 Cloudflare Tunnel 提供固定 HTTPS 地址。

## Cloudflare 预配置

1. 在 Cloudflare Zero Trust 创建一个 Tunnel。
2. 为 Tunnel 添加 Public Hostname，例如 `fgui-test.example.com`。
3. Service 设置为 `HTTP`，URL 设置为 `127.0.0.1:8780`。
4. 复制该 Tunnel 的 Windows service token。

## 目标 Windows 要求

- Windows 10/11 或 Windows Server 2019+，64 位。
- 管理员 PowerShell。
- Python 3.11 或更高版本，并可执行 `py -3` 或 `python`。
- 可访问 PyPI、GitHub 和 Cloudflare。

## 安装

解压部署 ZIP，在管理员 PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install.ps1
```

脚本会交互询问 HTTPS 地址和 Tunnel token。token 仅传给 Cloudflare 官方 service installer，
不会写入本项目配置、插件、日志或诊断文件。

安装成功后，测试插件位于：

`C:\ProgramData\FigmaToFGUI\release\Figma-to-FairyGUI-plugin.zip`

将这个插件 ZIP 发给测试者。服务器必须保持开机，Cloudflare Tunnel、Gateway 和 Writer 三项服务必须运行。

## 验证与日志

```powershell
Invoke-WebRequest https://fgui-test.example.com/health
Get-ScheduledTask FigmaToFGUI-Writer,FigmaToFGUI-Gateway
Get-Service cloudflared
Get-Content C:\ProgramData\FigmaToFGUI\logs\writer.log -Tail 100
Get-Content C:\ProgramData\FigmaToFGUI\logs\gateway.log -Tail 100
```

数据、SQLite、资源和生成 ZIP 均保存在 `C:\ProgramData\FigmaToFGUI\data`，升级时不得删除。
