# Git clone 后在 Windows 本机使用

这条路径不部署服务器、不使用 Cloudflare，也不开放局域网端口。每位同事在自己的电脑运行一份
Writer，Figma 插件只访问该电脑的 `http://localhost:8765`。

## 仓库发布方

把当前分支推送到团队可访问的私有 Git 仓库。不得提交 `.local-run/`、`.local-acceptance/`、token、
生成数据或测试用户的 Figma 内容。

## 使用方

在 PowerShell 中 clone 到纯英文短路径：

```powershell
git clone <private-repository-url> C:\src\figma-to-fgui
cd C:\src\figma-to-fgui
.\启动本机版.cmd
```

也可以在资源管理器中双击 `启动本机版.cmd`。首次运行会：

1. 缺少时通过 winget 安装 Python 3.11 和 Node.js LTS；
2. 在 `.local-run/venv` 安装 Writer；
3. 按两个锁文件安装并构建前端；
4. 在 `.local-run/` 生成仅限本机的随机插件 token；
5. 生成绑定 `http://localhost:8765` 的本机插件；
6. 在 `127.0.0.1:8765` 启动 Writer。

首次运行后，在 Figma Desktop 中选择 `Plugins → Development → Import plugin from manifest...`，
导入终端打印的文件：

```text
C:\src\figma-to-fgui\.local-run\plugin\manifest.json
```

以后使用时只需再次双击 `启动本机版.cmd`，并在使用插件期间保持终端窗口开启。关闭窗口或按
`Ctrl+C` 会停止本机 Writer。

本机数据保存在 `.local-run/data`，该目录已被 Git 忽略。更新代码后重新运行脚本即可更新依赖、
重建插件并启动服务。

## 常见问题

- 路径包含中文：重新 clone 到 `C:\src\figma-to-fgui`，不要放桌面或中文用户名下的深层目录。
- 公司网络阻止依赖下载：需要允许访问 Python package index 和 npm registry，或由团队提供内部镜像。
- 8765 被占用：先关闭旧的 Writer 窗口；不要为了绕开冲突修改插件或服务端其中一侧的端口。
- Figma 显示旧界面：删除旧开发插件，然后重新导入 `.local-run\plugin\manifest.json`。
