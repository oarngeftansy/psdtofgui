# 通用 UIR 与 FairyGUI 高保真适配设计

日期：2026-08-17

## 1. 目标

建立 `Figma → UIR → FairyGUI` 的稳定转换边界。UIR Core 保持目标引擎无关，但第一版必须完整承载 FairyGUI 生成所需的信息；FairyGUI 专属概念进入 Profile 与项目 Binding，不污染 Core。

第一版交付目标不是覆盖所有 UI 引擎，而是：

1. 用通用 UIR 准确表达当前 Figma 输入。
2. 由确定性 FairyGUI Adapter 生成 FairyGUI 6.1.4 可打开、可编辑、可复用的工程。
3. 无法原生表达的最小子树显式降级为图片，不允许静默丢失。
4. 所有不确定组件映射在生成前完成确认。

## 2. 架构选择

采用三层模型：

```text
UIR Core
  通用的源事实、语义、布局、视觉、组件、状态、资源与诊断

FGUI Profile
  FairyGUI 能力、XML 结构、Controller、Gear、List、九宫格等适配规则

Project Binding
  当前工程真实 package/component/resource ID、属性映射和人工确认结果
```

纯通用模型容易削弱 FairyGUI 表达能力；纯 FairyGUI 模型又无法扩展。本设计允许 Profile 扩展，但禁止把 `packageId`、`src`、`gearDisplay` 等字段写入 Core 节点。

## 3. 数据流

```text
Figma 当前选择
  → 确定性读取与规范校验
  → UIR Source Facts
  → 规则/模型产生 Semantic Candidates
  → 组件映射与人工确认
  → Confirmed UIR
  → 命名阶段
  → 资源计划与导出
  → FairyGUI Profile + Project Binding
  → 确定性 XML/资源生成
  → 结构、引用、视觉和行为校验
```

模型只允许提出语义候选，不得写 FairyGUI XML。FairyGUI Adapter 只消费已确认的 UIR，不重新读取 Figma，不调用模型，也不重新猜测组件。

## 4. UIR v1 文档结构

```json
{
  "schemaVersion": 1,
  "documentId": "uir_<stable-id>",
  "source": {
    "kind": "figma",
    "revision": "<snapshot-sha256>",
    "selectionId": "<selection-id>",
    "capturedAt": "<iso-8601>"
  },
  "roots": ["node:<id>"],
  "nodes": {},
  "componentDefinitions": {},
  "assets": {},
  "mappingDecisions": {},
  "diagnostics": [],
  "extensions": {}
}
```

`nodes`、`componentDefinitions`、`assets` 和 `mappingDecisions` 使用稳定逻辑 ID 的字典，避免数组重排破坏增量比较。`roots` 与每个节点的 `children` 保留确定的渲染顺序。

## 5. UIR Core 节点

每个节点至少包含：

```json
{
  "id": "node:<stable-id>",
  "source": {
    "nodeId": "<figma-node-id>",
    "type": "INSTANCE",
    "name": "通用一级按钮",
    "fingerprint": "<sha256>"
  },
  "semantic": {
    "name": "primaryAction",
    "role": "button",
    "status": "confirmed",
    "decisionRef": "decision:<id>"
  },
  "parentId": "node:<parent>",
  "children": [],
  "zIndex": 4,
  "geometry": {
    "localTransform": [1, 0, 0, 1, 152, 1562],
    "resolvedBounds": {"x": 152, "y": 1562, "width": 776, "height": 132},
    "rotation": 0,
    "opacity": 1
  },
  "layout": {},
  "visual": {},
  "text": null,
  "component": {},
  "interactions": [],
  "conversion": {}
}
```

约束：

- `source` 永远保存原始事实；命名阶段不得覆盖 `source.name`。
- 同时保存源变换和求值后坐标。FairyGUI v1 优先使用求值坐标，源布局语义用于校验与未来适配。
- `semantic.status` 只能是 `candidate | confirmed | rejected | fallback`。
- `conversion.mode` 只能是 `native | componentReference | existingResource | rasterFallback | unsupported`。
- `unsupported` 阻止生成；`rasterFallback` 必须拥有资源计划和原因。

## 6. 组件定义与实例

组件定义与实例分离：

- `componentDefinitions` 保存组件属性定义、Variant 维度、默认值和定义根节点。
- 实例节点只保存 `definitionRef`、`variantProperties`、`overrides`。
- 嵌套实例继续引用定义，不复制为失去来源关系的普通子树。
- Component Set 的多维 Variant 在 Core 中表达为通用状态维度；如何变成 FairyGUI Controller 由 FGUI Profile 决定。

## 7. 映射决策

已迁入的 Skill 映射作为候选来源，不作为真值。每项决策包含：

```json
{
  "id": "decision:<id>",
  "nodeId": "node:<id>",
  "semanticRole": "button",
  "candidateKey": "common_primary_button",
  "status": "verified",
  "evidence": ["exactFigmaName", "projectComponentExists"],
  "confidence": 1,
  "source": "rule",
  "humanDecision": null
}
```

处理规则：

- `verified`：自动复用目标组件。
- `missing`：自动采用最小子树 PNG 降级，不阻止生成。
- `conflict`：生成前必须选择目标组件或“本次使用 PNG”。
- 人工决定绑定到当前工程映射版本并记录审计信息，下次可复用。
- Skill 中的旧 package/component ID 仅是 `legacyHint`；真实 ID 必须来自当前工程索引。

## 8. 命名阶段

命名是独立、确定性的纯函数：

```text
sourceName → semanticName → targetName
```

- 保留原始名称。
- 依据可版本化命名规则生成语义名和目标名。
- 显式标注优先于规则；规则优先于模型建议。
- 检测非法字符、保留字、重复名称和路径穿越。
- 名称冲突必须生成诊断，不允许靠随机后缀静默处理。

## 9. 资源计划

资源表与节点树分离，每个资源包含：

- 稳定 `assetId`
- `sourceNodeId` 或 `imageRef`
- 导出格式、比例、裁剪、九宫格信息
- 内容哈希与导出参数哈希
- 建议名称和目标相对路径
- 消费节点列表
- `native | existingResource | rasterFallback` 决策与原因

生成前完成去重、尺寸检查和目标路径冲突检查。复杂效果只栅格化最小安全子树；文本和已确认组件不得被无故合入图片。

## 10. FGUI Profile

FGUI Profile 负责把通用语义转换为：

- Panel、Component、Button、Label、List、Loader、Text、Image
- Component Set 状态维度 → Controller 页面
- 节点状态条件 → `gearDisplay`
- 父子状态联动 → `gearFrame`
- 重复项 → `List + Render`
- Auto Layout 的已解析结果 → 坐标、`colGap`、`lineGap` 或 Relation
- 九宫格 → `scale9grid`
- 资源引用 → `src/pkg/fileName/url`

Profile 只描述能力与转换规则，不保存当前工程 ID。

## 11. Project Binding

Project Binding 来自上传 FairyGUI 工程的实时扫描：

- 包名、包 ID、包根目录
- 组件名、组件 ID、XML 路径、Controller 定义
- 资源名、资源 ID、类型、九宫格信息
- UIR 组件语义到真实组件的已确认映射
- Figma Variant 值到 Controller 页面的映射

生成器不得使用未验证的 Binding。工程变化导致 ID、路径或 Controller 漂移时，旧 Binding 自动失效并重新确认。

## 12. 插件确认界面

“组件映射检查”位于上传完成、正式生成之前，只显示当前选择实际涉及的映射：

- 已验证：展示 `Figma → UIR → FGUI`，自动继续。
- 缺失：说明将采用 PNG，不要求操作。
- 冲突：提供真实 FGUI 组件候选下拉框和“本次使用 PNG”。

未解决冲突时禁用继续生成。确认后保存 UIR 决策，再进入确定性生成。

## 13. 诊断与恢复

每条诊断包含稳定错误码、严重级别、公开消息、节点引用、规则版本和建议动作。不得暴露访问令牌、本地绝对路径、图片哈希或内部异常。

- 网络或服务失败：保留当前确认选择并允许重试。
- 工程在确认后变化：拒绝旧 Binding，重新扫描。
- 资源导出失败：仅允许有明确替代资源时继续，否则阻止生成。
- XML/引用校验失败：不得提供“成功下载”。

## 14. 增量与所有权

UIR v1 先建立增量基础，不承诺第一批实现完整 patch：

- 文档保存 `source.revision`。
- 节点和资源保存 fingerprint/hash。
- 生成记录区分工具拥有文件与用户维护文件。
- 不采用 UnityFigmaBridge 当前的全量删除重建方式。
- 后续增量生成只替换 fingerprint 变化且由工具拥有的产物。

## 15. 验收标准

### Schema

- UIR JSON Schema 能拒绝未知字段、无效枚举、悬空引用和无序/重复子节点。
- 同一输入和规则版本连续生成的 UIR 字节级一致。
- UIR Core 中不存在 FairyGUI 专属字段。

### FairyGUI 结构

- 生成工程可由 FairyGUI 6.1.4 打开。
- package.xml、组件 XML、资源引用、Controller、Gear、List 和九宫格全部通过自动校验。
- 已验证组件生成真实引用；缺失映射稳定 PNG 降级；冲突未确认时无法生成。

### 视觉与行为

- “村庄升阶”作为端到端 golden fixture，保存固定源快照、期望 UIR、期望工程和截图。
- 坐标和尺寸相对 Figma 误差不超过 1px；图片资源尺寸与声明一致。
- 字体、字号、对齐、描边和层级逐项校验。
- Component Set、Variant、按钮和 List 保留可用语义，不以整页截图冒充。
- 视觉截图差异阈值单独版本化；超过阈值即失败而非只给警告。

## 16. 第一实施批次边界

第一批只实现：

1. UIR v1 Pydantic 模型与 JSON Schema。
2. 当前 NormalizedNode → UIR Source Facts 的确定性转换。
3. 候选组件映射写入 `mappingDecisions`。
4. UIR 结构、引用和确定性校验。
5. “村庄升阶”的最小 golden UIR fixture。

第一批不实现插件确认 UI、完整 FGUI Adapter 重写、其他引擎 Adapter 或完整增量 patch；它们建立在稳定 UIR 契约之后。

## 17. 参考依据

详细研究见 `docs/research/2026-08-17-uir-reference-research.md`。主要一手依据为 UnityFigmaBridge 的节点模型、组件/实例映射、资源导出流水线和示例工程；Frame to PSD 与 Figma Community 页面因无公开源码或访问受限，仅作为产品方向参考，不作为 Schema 事实来源。
