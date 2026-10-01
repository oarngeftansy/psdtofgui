# Figma to FGUI 团队转换平台设计

## 1. 背景与目标

现有 `figma-to-fgui` Skill 是一份约 3026 行的单文件操作规范，混合了转换规则、历史缺陷、工具调用、项目绝对路径、示例和校验流程。它适合作为经验资料，但不适合作为团队可部署工具的唯一运行实现。

本项目将其改造为可部署的团队平台：用户在 Figma 中框选节点，服务器完成确定性转换和检查，Windows 本地应用安全地更新选定的 FGUI 工程。Codex 插件提供诊断、维护和辅助调用能力，但不是核心运行前提。

首版范围：

- 仅支持 Windows 客户端。
- Figma REST API 是核心数据入口。
- 支持 Figma Plugin、Web Console、转换服务器和 Windows Agent。
- 自动写入前必须预览、确认、备份和校验。
- 不通过 UI 自动化操纵 FairyGUI 编辑器。
- 不在首版直接修改 Git 仓库或创建分支。

## 2. 用户流程

1. 用户安装 Figma Plugin 和 Windows Agent。
2. 用户在 Windows Agent 中绑定一个或多个本地 FGUI 工程目录。
3. 用户在 Figma 中框选一个 Frame、Component、Component Set 或 Instance。
4. Figma Plugin 提交 `fileKey`、`nodeIds` 和目标 `projectId`。
5. 服务器通过 Figma REST API 获取节点、组件和资源数据。
6. 转换服务器运行原子转换管线并生成校验报告与 changeset 预览。
7. 用户在 Web Console 或 Figma Plugin 中审查变更并确认应用。
8. Windows Agent 下载已签名的 changeset，验证工程快照和目标路径。
9. Agent 备份受影响文件，执行原子写入和本地复验。
10. 成功后 Agent 通知用户重新加载 FairyGUI 工程；失败则自动回滚。

## 3. 总体架构

系统由五个边界清晰的部分组成：

### 3.1 Figma Plugin

- 使用 TypeScript 和 Figma Plugin API。
- 读取当前 selection，提交节点标识与必要上下文。
- 展示项目选择、任务状态和简要错误。
- 不保存 Figma Access Token。
- 不直接读取或修改本地 FGUI 工程。

### 3.2 Web Console

- 使用 React、TypeScript 和 Vite。
- 展示任务、资源预览、文件差异、规则命中和校验报告。
- 承担变更确认入口。
- 首版不引入复杂设计系统。

### 3.3 API Server 与 Conversion Worker

- 使用 Python 3.11+、FastAPI 和独立 Worker。
- API Server 负责认证、任务、项目、预览、审批和 changeset 分发。
- Worker 负责 Figma 数据读取、转换、资源生成和校验。
- 首版使用 PostgreSQL 作为任务状态与 Worker 领取机制，不引入 Redis。
- 临时工件与 changeset 存放在 MinIO 或兼容 S3 的对象存储中。

### 3.4 Windows Agent

- 使用 C# 和 .NET 8，发布为 Windows 自包含单文件应用。
- 作为托盘程序运行，管理本地工程绑定。
- 使用 Windows Credential Manager 保存设备凭证。
- 负责工程快照、备份、原子写入、回滚和系统通知。
- 默认禁止服务器执行本地命令。
- 只能修改用户明确绑定的工程目录。

### 3.5 Codex Plugin

- 包含 `.codex-plugin/plugin.json`、总入口 Skill 和维护/诊断 Skill。
- 用于依赖诊断、规则解释、映射维护和失败分析。
- 可调用服务端 API 或后续的 MCP 适配器。
- 不携带 Figma Token，不直接写入 FGUI 工程。

## 4. 仓库结构

采用单仓库管理前后端、Agent、规则、协议和测试：

```text
figma-to-fgui/
├── apps/
│   ├── figma-plugin/
│   ├── web-console/
│   ├── windows-agent/
│   └── api-server/
├── packages/
│   ├── conversion-core/
│   ├── fgui-parser/
│   ├── figma-client/
│   ├── rule-engine/
│   ├── validators/
│   └── contracts/
├── rules/
│   ├── classification.yaml
│   ├── naming.yaml
│   ├── resources.yaml
│   ├── fgui-xml.yaml
│   ├── validation.yaml
│   └── compatibility/
├── mappings/
├── plugins/
│   └── codex/
├── tests/
│   ├── fixtures/
│   ├── golden/
│   └── integration/
├── deploy/
│   ├── docker/
│   └── compose.yaml
└── docs/
```

## 5. 原子转换内核

每一步接收不可变输入，输出新的 JSON 工件和结构化诊断。除 Windows Agent 的 apply 阶段外，任何步骤都不得修改真实 FGUI 工程。

### 5.1 `fetch`

输入 Figma `fileKey` 和 `nodeIds`，输出原始节点 JSON、组件信息及图片引用清单。负责认证、限流、重试和缓存，不处理 FGUI 语义。

### 5.2 `normalize`

把 Figma 数据转换为稳定的内部节点模型，包括：

- ID、名称、节点类型
- bounds、rotation 和 source order
- children、fills、strokes、effects
- 文本内容与样式
- Component Set、properties 和 variant 信息

### 5.3 `index-project`

扫描只读工程快照，构建包、组件、图片、资源 ID、路径和通用组件索引。

### 5.4 `classify`

判定 Panel、Button、Render、SubComponent、Component Set、通用组件引用或内联元素。每个结论必须记录规则 ID、规则版本、证据和置信度。

### 5.5 `plan-assets`

形成资源计划，确定资源复用、Figma 下载、节点合并、遮罩裁剪、九宫格合成、颜色染色和命名，不直接产生文件。

### 5.6 `render-assets`

按资源计划生成图片，并记录宽高、MD5、SHA-256、透明边界和来源节点。

### 5.7 `generate`

在隔离暂存区生成组件 XML、图片、`package.xml` 补丁及 `handoff.yaml`。

### 5.8 `validate`

检查 XML、资源引用、路径、坐标、尺寸、z-order、Controller、Component Set、命名及规则一致性。

### 5.9 `build-changeset`

对比原始工程快照和暂存区，生成可预览、可签名、可回滚的变更包。

## 6. 规则与映射系统

规则优先级固定为：

```text
项目覆盖规则 > 团队规则 > 插件默认规则 > 旧项目兼容规则
```

每条机器规则必须包含：

- `id`
- `version`
- `priority`
- `when`
- `action`
- `severity`
- `explanation`
- `testCases`

原 Skill 内容按以下方式迁移：

| 原内容 | 新位置 |
|---|---|
| 明确转换规范 | `rules/*.yaml` |
| Figma/FGUI 映射 | `mappings/*.json` |
| 历史缺陷与 Test 案例 | 回归测试 |
| 操作流程 | 编排 Skill 与产品文档 |
| 解释性说明 | `docs/` |
| 单一旧工程特殊行为 | `rules/compatibility/` |

正式规则不得继续依赖个人路径或特定 MCP 工具名。

## 7. API 与通信协议

核心接口：

```text
POST /v1/selections
POST /v1/agents/register
POST /v1/projects/snapshot
GET  /v1/jobs/{id}
GET  /v1/jobs/{id}/preview
POST /v1/jobs/{id}/approve
GET  /v1/jobs/{id}/changeset
POST /v1/jobs/{id}/apply-result
```

所有跨组件数据结构由 `packages/contracts` 统一定义并显式版本化。

`changeset` 仅允许：

- 在工程根目录下创建相对路径文件
- 替换明确列出的相对路径文件
- 删除明确标记为工具生成的文件
- 更新 `package.xml`
- 为每个文件携带修改前和修改后的 SHA-256

它不得包含绝对路径、`..` 路径、任意本地命令或未声明的文件操作。

## 8. Windows Agent 写入事务

Agent 应按以下事务流程更新工程：

1. 检查 changeset 签名、用户、团队、设备和 `projectId`。
2. 将每个目标路径规范化，确认最终路径仍在绑定工程根目录内。
3. 比对当前文件哈希与任务快照；发现并发修改时拒绝覆盖。
4. 在 `.figma-to-fgui/backups/{jobId}/` 创建受影响文件备份。
5. 将新内容写入同目录临时文件。
6. 对临时 XML、图片和引用执行本地复验。
7. 使用原子替换提交全部变更。
8. 任一步失败时恢复备份，并上报结构化错误。
9. 成功后显示系统通知，提示 FairyGUI 重新加载工程。

首版不模拟鼠标或键盘操作 FairyGUI。只有在获得稳定、官方或可验证的刷新机制后，才增加自动刷新。

## 9. 安全设计

- Figma Access Token 只保存在服务器 Secret 中。
- Figma Plugin 和 Web 浏览器不持有该 Token。
- Agent 设备凭证保存于 Windows Credential Manager。
- 任务必须同时匹配用户、团队、项目和 Agent 设备。
- Agent 默认不能访问未绑定目录。
- Agent 默认不能执行服务器下发的命令。
- changeset 必须签名，并带有效期与一次性下载授权。
- 对象存储中的原始数据、图片和变更包必须设置过期清理策略。
- 服务端记录 Figma 来源、规则版本、快照哈希、审批人和修改清单。
- 删除只允许作用于已登记的工具生成文件，首版可进一步限制为不自动删除。

## 10. 依赖审计

### 10.1 现有 Skill 的显式和隐式依赖

| 依赖 | 当前检测 | 迁移方案 |
|---|---|---|
| `FIGMA_ACCESS_TOKEN` | 当前环境未设置 | 迁移至服务器 Secret |
| Figma REST API | Skill 明确依赖 | 保留并封装为 `figma-client` |
| `figma_fgui_mapping.json` | 声明路径不存在 | 纳入版本化 `mappings/` |
| FGUI assets 路径 | 当前机器存在 | 由 Agent 项目绑定替代绝对路径 |
| `通用组件.md` | 当前机器存在 | 转为结构化 registry，Markdown 作为导出物 |
| Python | 3.11.9 已安装 | 服务端固定 Python 版本 |
| Pillow/PIL | 当前环境未安装 | 纳入服务端正式依赖 |
| PowerShell | Windows PowerShell 可用 | 核心校验改为跨平台 Python |
| `System.Drawing.Image` | 依赖 Windows API | 改为 Pillow |
| `get_figma_data` | 当前无同名工具 | 移除硬依赖 |
| `download_figma_images` | 当前无同名工具 | 改为 REST API 实现 |
| Figma AI Bridge MCP | 当前无该工具 | 降级为可选适配器 |
| `C:\Users\Swind` | 当前机器不存在 | 删除硬编码 |
| `D:\Figma\ToFgui\...` | 当前机器不存在 | 删除硬编码 |

当前 Codex 环境提供其他 Figma 连接器能力，但核心转换不能绑定这些工具的名称或响应格式。

### 10.2 服务端依赖

- Python 3.11+
- FastAPI、Uvicorn
- Pydantic
- HTTPX
- Pillow
- lxml
- PyYAML
- PostgreSQL 驱动
- Alembic
- MinIO/S3 SDK
- PostgreSQL
- MinIO 或兼容 S3 的存储
- Docker 和 Docker Compose
- 到 Figma API 与资源下载域名的网络访问

### 10.3 Windows Agent 依赖

- .NET 8 SDK 用于构建
- Windows 自包含发布，运行端无需预装 .NET Runtime
- Windows Credential Manager
- 托盘、目录选择、HTTPS/WebSocket、XML 和 ZIP 能力
- 第二阶段增加代码签名、安装包与自动更新

### 10.4 前端与插件依赖

- Node.js LTS
- TypeScript
- React、Vite
- Figma Plugin API
- esbuild 或等价的轻量构建工具
- Codex Plugin 的标准 manifest 和 Skill 目录

## 11. 原 Skill 中待裁决的冲突

工具化前必须将以下冲突变成单一、可测试的正式规则：

1. 前文允许部分节点生成 `<graph>`，后文又要求完全禁止 `<graph>`。
2. 一处要求 Component Set 每个状态导出完整资源，另一处允许相同纹理仅保留一份。
3. 一处强调坐标和尺寸禁止估算，其他部分又规定小数统一四舍五入。
4. `gearFrame` 的父子 Controller 表述和示例容易产生不同解释。
5. 规则、例外、用户偏好和历史修复没有明确优先级。

推荐正式裁决：

- 默认视觉节点输出图片；只有无圆角、无阴影、无描边、无透明叠加的简单几何装饰可由项目规则显式允许 `<graph>`。
- 语义状态必须完整存在于组件模型中；像素完全相同的资源允许按哈希去重，但保留状态到资源的映射记录。
- 所有内部计算保留浮点精度；写入 FGUI XML 时采用统一、可配置的取整策略，并在诊断中记录修正值。
- 用协议级测试固定 `gearDisplay`、`gearFrame` 与 Controller 的语义。

## 12. 错误处理与可观测性

诊断统一分为：

- `ERROR`：禁止生成可应用 changeset。
- `WARNING`：允许人工确认后继续。
- `INFO`：记录规则命中、资源复用和自动修正。

每条诊断包含：

- 代码和严重级别
- Figma 节点 ID
- 目标 FGUI 文件或资源
- 命中规则及版本
- 人类可读解释
- 可选修复建议

任务阶段、耗时、重试、缓存命中、资源数量和 changeset 应用结果应进入结构化日志。Token、设备凭证和原始敏感数据不得写入日志。

## 13. 测试策略

### 13.1 单元测试

覆盖颜色、坐标、ID、路径、命名、Controller 映射、哈希和 changeset 规范化。

### 13.2 规则测试

每条规则至少包含一个命中和一个不命中案例。

### 13.3 Golden 测试

固定的脱敏 Figma JSON 和 FGUI fixture 必须生成确定一致的 XML、资源计划、诊断和 changeset。

### 13.4 端到端测试

覆盖 Figma 测试文件、服务端任务、changeset、Windows Agent 临时工程、写入、复验和回滚。

### 13.5 必需回归案例

- `<graph>` 判定冲突
- 单维度与多维度 Component Set
- `gearDisplay` 与 `gearFrame`
- List 的 `colGap` 和 `lineGap`
- 含 TEXT 的图标 GROUP 禁止整体合并
- `cropTransform` 异常
- 九宫格 `fillOverrideTable`
- rotation、pivot 和左上角坐标
- 资源 ID 长度
- 图片尺寸与 XML 声明不一致
- Primary 与 Primary_Cost 按钮选择
- 工程并发修改造成哈希冲突
- 写入中途失败后的完整回滚
- 路径穿越与工程目录越界

## 14. 部署拓扑

团队服务器运行：

- Web Console
- FastAPI
- Conversion Worker
- PostgreSQL
- MinIO

使用 Docker Compose 部署。外部流量统一经过 HTTPS 反向代理。API Server 与 Worker 使用同一套版本化 contracts、rules 和 conversion-core。

Windows 用户机器运行：

- Figma 桌面端及 Figma Plugin
- FairyGUI
- Windows Agent

## 15. 分阶段交付

### 阶段一：转换内核基线

- 提取正式规则、映射和回归案例。
- 建立离线 `Figma JSON + FGUI fixture -> changeset` 管线。
- 完成确定性、错误分级和核心 Golden 测试。

### 阶段二：服务端与 Web 预览

- 完成 FastAPI、任务模型、Worker、PostgreSQL 和 MinIO。
- 支持通过 Figma URL/node ID 发起任务。
- 完成文件差异、资源预览、诊断和审批。

### 阶段三：Windows Agent

- 完成工程绑定、快照、设备认证、备份、哈希校验、原子写入和回滚。
- 完成系统通知和临时测试工程的端到端验证。

### 阶段四：Figma Plugin

- 支持在 Figma 中提交当前选择。
- 支持选择已绑定工程、查看任务状态及主要诊断。

### 阶段五：Codex Plugin 与团队发布

- 完成 Codex 诊断、规则维护和失败分析能力。
- 完成 Docker 镜像、Windows 安装包、发布版本和团队安装文档。

## 16. 首版验收标准

- 用户能在 Figma 中框选一个节点并选择已绑定的 FGUI 工程。
- 服务端能生成可审查的 changeset。
- 存在 `ERROR` 时禁止应用。
- 用户确认后 Agent 自动备份并更新本地工程。
- 工程文件在任务期间发生变化时拒绝覆盖。
- 应用失败时能够完整回滚。
- 同一输入、工程快照和规则版本产生确定性结果。
- 每次任务记录 Figma 来源、规则版本、快照哈希、审批人和修改清单。
- Agent 无权修改绑定工程目录之外的文件。

## 17. 非目标

首版不包含：

- macOS Agent。
- 自动提交 Git 分支或 Pull Request。
- FairyGUI UI 鼠标键盘自动化。
- 让服务端执行任意本地命令。
- 把现有 3026 行 Skill 原样搬入新插件。
- 用特定 MCP 工具作为核心转换依赖。
