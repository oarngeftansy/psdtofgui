# 通用 FairyGUI Generation Plan 设计

日期：2026-08-17

## 1. 目标与范围

本阶段建立 `UIR → FGUI Generation Plan` 的确定性转换层。它描述“要生成什么 FairyGUI 对象和资源”，但不绑定当前工程的包、组件或资源 ID。

第一版覆盖：

- Panel / Component 容器。
- Text / 可表达的 RichText。
- Image / Loader。
- 已验证组件引用。
- PNG 最小子树降级。
- 坐标、尺寸、旋转、透明度和层级。
- 九宫格资源信息。
- 矩形、圆角矩形、简单图片遮罩以及复杂遮罩降级。

Controller、Gear、List 和复杂 Auto Layout 语义不在本批次实现。它们必须产生显式能力诊断，不得静默丢失。

“村庄升阶”只是端到端回归样例，不是转换规则来源。任何 Profile 规则不得按页面名称、样例节点 ID 或样例尺寸分支。

## 2. 系统边界

```text
UIR Core
  → FGUI Capability Analyzer
  → FGUI Plan Compiler
  → FGUI Generation Plan
  → Project Binding（下一批）
  → FGUI XML Writer（下一批）
```

### 2.1 Capability Analyzer

对每个 UIR 节点进行纯函数判定，输出 `native | rasterFallback | unsupported` 及证据。它不写 XML，不读取 Figma，不调用 AI，不扫描具体 FairyGUI 工程。

### 2.2 Plan Compiler

将已验证 UIR 和能力判定编译为稳定的目标计划。相同 UIR 字节、Profile 版本和规则版本必须产生字节级相同的计划。

### 2.3 后续边界

Project Binding 才能填入当前工程真实的 package/component/resource ID。XML Writer 只消费已验证的 Plan + Binding，不重新做能力推断。

## 3. Generation Plan 契约

### 3.1 PlanDocument

包含：

- `schemaVersion`
- `documentId`
- `sourceUirSha256`
- `profileVersion`
- `ruleVersion`
- 有序根节点
- 节点、资源、遮罩和能力决策字典
- 诊断
- `bindable`：不存在阻断错误时为 `true`

### 3.2 PlanNode

每个节点包含稳定 ID、UIR 节点引用、父子关系、层级、目标对象类型和专用计划引用。第一版目标类型是：

- `container`
- `text`
- `richText`
- `image`
- `loader`
- `componentReference`
- `rasterSubtree`

### 3.3 专用计划

- `TransformPlan`：已解析坐标、尺寸、旋转和透明度。
- `TextPlan`：原文、字体候选、字号、颜色、对齐和可表达的文本样式。
- `ResourcePlan`：逻辑资源 ID、类型、导出参数、内容哈希、九宫格和消费节点。
- `ComponentReferencePlan`：仅保存已验证的通用 candidate key 和变体值。
- `MaskPlan`：遮罩类型、遮罩源、内容节点、层级范围和转换方式。
- `CapabilityDecision`：状态、稳定规则 ID、版本、证据、原因和阻断性。

## 4. 强制隔离

Plan 中禁止出现：

- 真实 `packageId`、`componentId`、`src`、`pkg`。
- Figma 访问令牌、本地绝对路径和图片原始字节。
- 样例页面名称或样例节点特判。
- 未验证组件映射的 legacy ID。

未解决映射冲突产生 `unsupported`，不得选择任意目标。

## 5. 通用能力规则

| UIR 内容 | Plan 结果 | 转换状态 |
|---|---|---|
| Frame / Group / Component | `container` | `native` |
| 普通 Text | `text` | `native` |
| 可表达多段文本 | `richText` | `native` |
| 普通图片 | `image` 或 `loader` | `native` |
| 已验证组件 | `componentReference` | `native` |
| 可安全合成的复杂视觉 | `rasterSubtree` | `rasterFallback` |
| 数据损坏、映射冲突或无可靠路径 | 无可绑定节点 | `unsupported` |

`native` 必须有相应原生计划。`rasterFallback` 必须有明确资源计划和原因。`unsupported` 必须产生阻断诊断。

## 6. 遮罩规则

### 6.1 原生路径

- 矩形和圆角矩形裁剪生成 `nativeClip` MaskPlan。
- 边界确定、层级合法的简单图片遮罩生成 `nativeMask` MaskPlan。

### 6.2 降级路径

矢量布尔、渐变、模糊、特殊混合模式遮罩仅在可完整导出最小安全子树时生成 `rasterSubtree`。该子树内部节点不再重复生成原生对象。

### 6.3 阻断路径

遮罩源缺失、跨非法父级、边界无法求值或资源无法导出时产生 `unsupported`。不得忽略遮罩后继续生成。

## 7. 九宫格与文本

- 九宫格边界必须为正数矩形并且不超出资源尺寸。无效九宫格不得进入原生 Plan。
- 文字内容、字号、对齐和可表达样式必须保持源事实。
- 字体候选未绑定时保留通用逻辑名称；具体 FGUI 字体资源由 Project Binding 决定。
- 字体缺失且策略不允许降级时产生 `unsupported`。

## 8. 诊断与失败策略

每条诊断包含错误码、级别、UIR 节点 ID、能力规则版本、证据、建议操作和是否阻止 Binding。

稳定错误码包括：

- `fgui.mask.source_missing`
- `fgui.mask.invalid_hierarchy`
- `fgui.mask.raster_fallback`
- `fgui.component.mapping_conflict`
- `fgui.text.font_unresolved`
- `fgui.resource.nine_slice_invalid`
- `fgui.visual.effect_unsupported`

存在任何 `unsupported` 或 ERROR 时，PlanDocument 的 `bindable` 必须为 `false`。计划仍可序列化用于审查，但不得进入 Binding 或提供成功下载。

## 9. 验证

### 9.1 Schema

拒绝未知字段、非法枚举、目标工程 ID 泄漏和非确定集合。

### 9.2 引用完整性

检查根节点、父子关系、资源、遮罩、组件决策和重复所有权。

### 9.3 能力一致性

- `native` 必须对应原生 PlanNode。
- `rasterFallback` 必须对应 ResourcePlan。
- `unsupported` 必须对应阻断诊断。
- 被栟格化子树不得产生重复子节点。

### 9.4 确定性

相同 UIR、Profile 版本和规则版本连续生成的规范 JSON 与 SHA-256 必须相同。

## 10. 通用性测试矩阵

- 普通面板、嵌套容器、同名不同层级和渲染顺序。
- 普通文本、多段文本、对齐、描边和字体缺失。
- 图片、Loader、合法与非法九宫格。
- 已验证、缺失和冲突的组件映射。
- 矩形遮罩、图片遮罩、复杂遮罩降级与损坏遮罩。
- 多种页面名称、节点 ID、尺寸和合法节点组合。
- 扫描规则和代码，证明不存在“村庄升阶”或固定样例 ID 条件分支。
- “村庄升阶”只作为复杂页面端到端 golden fixture。

## 11. 验收标准

1. 同一 UIR 连续生成的 Plan 字节级相同。
2. Plan Core 不包含真实 FairyGUI 工程 ID 和本地绝对路径。
3. 所有降级都具有资源、原因和影响节点。
4. 所有无可靠路径的节点都会阻止 Binding。
5. 组件引用只使用已验证 candidate key。
6. 遮罩层级、范围和降级子树经引用验证。
7. 通用测试矩阵先通过，再使用“村庄升阶”验证复杂真实页面。

## 12. 后续阶段

本设计通过后，下一个独立规格是 Project Binding，再下一个是 XML Writer 与真实 FairyGUI 6.1.4 加载验证。Controller、Gear、List 和复杂 Auto Layout 将在基础 Plan 契约稳定后分批加入。
