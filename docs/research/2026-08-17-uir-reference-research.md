# Figma → UIR → FairyGUI：外部参考研究

日期：2026-08-17

## 结论先行

这四个参考中，真正可用于设计 UIR 的一手实现证据主要来自 `UnityFigmaBridge` 及其示例工程。它证明了一个稳健的转换系统应当把“源设计事实”“语义映射决策”“导出资源”“目标平台绑定”分层保存，而不是在生成 FairyGUI XML 时临时猜测。

建议 UIR 吸收以下原则：

1. 保留完整、有序的节点树，并同时保存稳定源 ID、父子关系和局部变换。
2. 组件定义与组件实例分离，实例只引用定义并记录 override。
3. 原始设计数据和目标平台决策分离；资源、组件映射、降级栅格化都应是显式决策。
4. 资源拥有稳定逻辑 ID、内容哈希、导出参数和目标路径，不能只依赖图层名称。
5. 命名是可配置的独立阶段，不能把目录路径、行为绑定和类型识别都塞进名称字符串。
6. 生成器只消费已验证 UIR；不确定项必须保留诊断与人工确认状态。
7. 增量更新应以稳定 ID 和内容哈希为基础。UnityFigmaBridge 当前仍是整体重建，它的源码甚至留下“以后只处理差异”的 TODO，因此不能照搬其同步策略。

## 证据边界

| 参考 | 可验证材料 | 可信范围 |
|---|---|---|
| Frame to PSD | [Figma Community 官方入口](https://www.figma.com/community/plugin/1645048489427217711/frame-to-psd) | 页面受 robots 限制，且没有找到公开源码；只能把“分层 PSD”视为产品方向，不能据此断言内部数据结构 |
| UnityFigmaBridge | [官方 GitHub 仓库](https://github.com/simonoliver/UnityFigmaBridge)、[README](https://github.com/simonoliver/UnityFigmaBridge#readme)、源码 | 可核对节点模型、组件映射、资源导出、Unity 绑定与同步策略 |
| Figma Unity Bridge Example | [Figma Community 官方入口](https://www.figma.com/community/file/1230440663355118588/figma-unity-bridge-example)、仓库内可导入的 [.fig 文件](https://github.com/simonoliver/UnityFigmaBridgeExample/tree/main/FigmaSource) | Community 页面无法直接读取；仓库中的源文件和生成结果可以验证输入/输出组织 |
| UnityFigmaBridgeExample | [官方 GitHub 仓库](https://github.com/simonoliver/UnityFigmaBridgeExample) | 可验证生成目录、Prefab 产物和行为绑定示例 |

## 1. Frame to PSD

### 可验证事实

官方 Figma Community 链接能够确认该产品是一个 Figma 插件入口，但当前研究环境无法读取其详情页，也没有发现与该插件 ID 对应的公开源码。因此无法从一手材料确认它怎样序列化图层、如何实现文字可编辑性、怎样表达 Smart Object，或是否支持增量更新。

### 可借鉴的原则

- UIR 不应只有“最终截图”，应保留可编辑语义：文本、图形、图片和分组至少要能够分别表达。
- 需要显式的 `renderMode` / `fallbackReason`：目标平台能够原生表达时保留结构，不能表达时再栅格化。
- 降级应发生在最小子树，而不是整页扁平化；这是从“分层目标格式”反推的合理产品原则，不是该插件源码事实。

### 不应据此采用的内容

- PSD 的 Smart Object、Photoshop blend mode 和图层效果不是 FairyGUI 概念，不能直接进入核心 UIR；最多放在源样式或目标适配扩展中。
- 在没有源码或官方字段说明时，不应仿造所谓“Frame to PSD schema”。
- PSD 是离线文档输出，不代表它解决了组件实例、运行时状态、跨包引用或增量同步。

## 2. UnityFigmaBridge

### 图层树与源数据

该项目直接反序列化 Figma 文件数据。它的节点模型保留 `absoluteBoundingBox`、`size`、`relativeTransform`、`clipsContent`、约束、透明度、Auto Layout 方向和 sizing mode；还保留 padding、item spacing、overflow、effects、mask、文本内容/样式以及实例的 `componentId`。这表明中间层不能只存最终 x/y/w/h，否则旋转、父子坐标、约束和布局语义都会丢失。[节点字段源码](https://github.com/simonoliver/UnityFigmaBridge/blob/main/UnityFigmaBridge/Editor/FigmaApi/FigmaApiData.cs#L730-L894)

项目还为导入过程建立了 `NodeLookupDictionary`，以源节点 ID 快速定位节点；这支持 UIR 使用 Figma node ID 作为 source identity，但不应把它直接当目标资源 ID。[导入上下文源码](https://github.com/simonoliver/UnityFigmaBridge/blob/main/UnityFigmaBridge/Editor/FigmaImportProcessData.cs#L15-L74)

**对 UIR 的启示：**

- 每个节点保存 `source.nodeId`、`source.type`、`children[]` 和稳定 sibling order。
- 同时保存 `geometry.localTransform` 与规范化后的 `layout.bounds`；绝对坐标仅作为核对信息。
- 裁剪、mask、overflow 和布局约束应是一级字段，不能从最终图片反推。

### 组件定义、实例与 override

README 明确区分 Figma Component 和 Component Instance：组件生成 Prefab，实例化对应 Prefab，并应用修改后的属性；也支持嵌套组件。[对象映射与特性](https://github.com/simonoliver/UnityFigmaBridge#how-figma-objects-map-to-unity)

源码使用 `Dictionary<string, ComponentMappingEntry>` 保存“Figma component node ID → Unity Prefab”，并单独记录缺失组件定义。这是“定义/实例分离”和“缺失状态显式化”的直接证据。[组件映射源码](https://github.com/simonoliver/UnityFigmaBridge/blob/main/UnityFigmaBridge/Editor/FigmaImportProcessData.cs#L76-L165)

**对 UIR 的启示：**

- `componentDefinitions` 与页面节点树分开。
- 实例包含 `componentRef`、`overrides`、`variantProperties`，而不是复制整个定义树后失去来源关系。
- 映射状态使用 `verified | missing | conflict | rasterFallback`，并保存候选、证据和人工决定。
- FairyGUI 的 package/component/resource ID 放在 `targetBinding.fgui`，不能污染通用节点类型。

### 布局坐标与响应式

README 表示它根据 Figma constraints 实现响应式，但不支持 `Scale` constraint；Auto Layout 会映射为 Unity 的垂直或水平 Layout Group，而且默认关闭，因为复杂布局可能出问题。[Responsive layout 与 Auto layout](https://github.com/simonoliver/UnityFigmaBridge#responsive-layout)

**可借鉴：** UIR 同时保存源 Auto Layout 参数和已经求值的确定性几何。FGUI 生成器可优先采用经过验证的固定坐标，之后再选择性生成 relations/list layout。这样既不丢语义，又不会因目标布局系统差异导致漂移。

**不适用：** Unity 的 RectTransform、Layout Group、Canvas safe-area 不是 FGUI XML 结构；不能直接照搬 Unity 锚点计算。

### 样式与字体

项目会映射 Figma 字体到 TextMeshPro 字体，缺失时可下载 Google Fonts，并为描边/阴影生成材质 preset。README 同时明确列出多个不支持项，例如多重 fill、部分 effect、非纯色 stroke、部分 shape 和 boolean operations。[字体策略](https://github.com/simonoliver/UnityFigmaBridge#fonts)、[不支持列表](https://github.com/simonoliver/UnityFigmaBridge#currently-unsupported)

**对 UIR 的启示：**

- 样式字段要保留原始 paint/effect/text run，而不是只保存一个扁平颜色。
- 增加 `capabilityDecision`：`native`、`existingResource`、`rasterize`、`unsupported`。
- 字体解析结果应记录“精确匹配/替代字体/栅格化”及原因，避免静默替换造成字号和排版漂移。

### 资源导出

README 说明 image fill 下载为 PNG，并以 Figma ID 命名；vector、只含 vector 的 frame、名称包含 `render` 的对象会请求 Figma 服务端栅格化。用户显式标记导出的对象还能用图层名表达目标目录路径。[资源导出](https://github.com/simonoliver/UnityFigmaBridge#exporting-assets)、[服务端渲染](https://github.com/simonoliver/UnityFigmaBridge#server-rendering)

导入器先收集实际使用的 image fill ID，批量请求 server render，然后生成下载队列并下载，最后才构建目标文档。[导入流水线源码](https://github.com/simonoliver/UnityFigmaBridge/blob/main/UnityFigmaBridge/Editor/UnityFigmaBridgeImporter.cs#L463-L520)

**可借鉴：**

- UIR 资源表应独立于节点树：`assetId`、`sourceNodeId/sourceImageRef`、`format`、`scale`、`crop`、`contentHash`、`targetPath`、`consumers[]`。
- 先形成资源计划和去重结果，再下载/生成；不要遍历到一个节点就写一次文件。
- 用户命名可以参与目标路径建议，但路径必须经过规范化、冲突检查和安全校验。

### Unity 绑定与命名

项目按名称自动绑定运行时代码：对象名与 MonoBehaviour 类名不区分大小写匹配；序列化字段会在固定深度内寻找同名对象；按钮点击可用属性指定对象名；名称包含 `Button` 或存在 prototype activate link 会添加 Button，名为 `SafeArea` 会附加专用行为。[Binding behaviours](https://github.com/simonoliver/UnityFigmaBridge#binding-behaviours)

**可借鉴：** 命名规范应产生稳定的 `semanticName` 和 `bindingKey`，并支持显式 annotation 覆盖启发式判断。

**不适用：** 不应把“包含 Button 字符串”等弱启发式直接作为确定性 FGUI 类型；深度为 2 的反射绑定、MonoBehaviour 和 Unity Button 状态也不是 FairyGUI Controller/gear 的等价物。UIR 应把 `semanticRole` 与显示名称分开，并要求低置信度结果人工确认。

### 增量更新

README 提到同步会替换既有组件和 screens，因此额外提供行为自动重绑。导入器源码则明确留下 TODO：“等改为只处理差异后，就不会移除现有文件”。也就是说，当前实现不是可靠的增量 patch，而是重新生成后恢复绑定。[同步说明](https://github.com/simonoliver/UnityFigmaBridge#binding-behaviours)、[整体重建 TODO](https://github.com/simonoliver/UnityFigmaBridge/blob/main/UnityFigmaBridge/Editor/UnityFigmaBridgeImporter.cs#L429-L436)

**对 UIR 的启示：** 不要复制它的全量覆盖。UIR 应加入 `schemaVersion`、`sourceRevision`、节点/资源 `fingerprint` 和 `generatedOwnership`，使生成器能够区分工具拥有的内容、用户维护的内容和已删除源节点。

## 3. Figma Unity Bridge Example Community file

Community 文件和桥接器 README 将其定位为可一键导入的示例输入；示例仓库保留了一份可重新导入 Figma 的 `.fig` 源文件。[桥接器 README](https://github.com/simonoliver/UnityFigmaBridge#readme)、[示例源文件目录](https://github.com/simonoliver/UnityFigmaBridgeExample/tree/main/FigmaSource)

由于 Community 页面本身无法读取，本研究没有把画布中的具体命名、层级或样式作为证据。可以确认的价值是：同一个样例同时保存“可复现实验输入”和“生成后的 Unity 工程”，适合作为 golden fixture。

**对本项目的启示：** 将“村庄升阶”建立为同样的端到端 fixture：固定 Figma 快照/插件导出 JSON、期望 UIR、期望 FGUI ZIP、编辑器截图和差异阈值。以后修改规则必须跑同一组 fixture，防止只修一个页面又破坏其他页面。

## 4. UnityFigmaBridgeExample

示例工程的生成目录清晰拆分为 `Components`、`ImageFills`、`Pages`、`Screens`、`ServerRenderedImages`、`Fonts` 和 `FontMaterialPresets`。[生成资产目录](https://github.com/simonoliver/UnityFigmaBridgeExample/tree/main/Assets/Figma)

README 还说明示例行为会按 Figma 节点名称自动附加：`TypeWriterText` 绑定到 SpeechBubble 内同名对象，`AnimatedShape` 绑定到对应对象，`CodeScreen` 展示字段和按钮事件绑定。[示例 README](https://github.com/simonoliver/UnityFigmaBridgeExample#attached-behaviours)

**可借鉴：**

- 测试输出应按定义、页面、屏幕、源图、栅格降级图和字体分区，便于审计来源与复用。
- UIR 应保存 binding intent，但具体 Unity 脚本或 FGUI Controller/Transition 应由目标适配层生成。
- 示例工程本身应纳入回归测试，而不只做人工演示。

**不适用：** Prefab、MonoBehaviour、TextMeshPro material、scene Canvas 和 C# 字段绑定均是 Unity 特有产物，不应进入通用 UIR 核心。

## 建议的 UIR v1 边界

```text
UIRDocument
├─ schemaVersion / sourceRevision / generatorVersion
├─ roots[]                         # 页面或选中 Frame
├─ nodes{}                         # 稳定 sourceNodeId 索引
│  ├─ kind / semanticRole / name
│  ├─ parentId / children[] / zIndex
│  ├─ geometry / layout / constraints / clip
│  ├─ visual / text / interactions
│  ├─ componentRef / overrides
│  └─ conversionDecision / diagnostics
├─ componentDefinitions{}
├─ assets{}
├─ mappings{}
└─ targetBindings.fgui{}
```

关键设计约束：

- `source` 字段保存 Figma 事实；`semantic` 保存规则或人工确认的含义；`targetBindings` 保存 FairyGUI 专属结果。
- 每项自动决策保存 `ruleId`、`confidence`、`status`、`evidence`；低置信度和冲突项在生成前确认。
- 名称清洗只修改 `semanticName/targetName`，永远保留 `sourceName`。
- 资源栅格化是显式、可追踪、可缓存的计划，不是生成 XML 时的隐式副作用。
- FGUI XML 生成器不得重新分析 Figma，也不得调用模型；它只读取已验证的 UIR。

## 对当前路线的直接建议

1. 先定义 UIR Schema 和 JSON Schema，再接 UI 确认层。
2. 把已迁入的 Skill 组件映射作为 `mappings` 的候选来源，而不是写死到节点转换器。
3. 第一版同时保存源变换和求值后坐标，FGUI v1 使用求值坐标，避免 Auto Layout 直接映射造成漂移。
4. 实现资源计划表和内容哈希，为后续增量更新打基础。
5. 用“村庄升阶”建立 source → UIR → FGUI 的 golden fixture，并加入视觉截图对比。
6. 暂不实现 UnityFigmaBridge 式名称反射绑定；先采用显式注解、已验证映射和人工确认。

