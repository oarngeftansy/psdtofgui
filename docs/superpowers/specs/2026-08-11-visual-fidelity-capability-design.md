# Figma→FairyGUI 视觉能力判定与最小栅格兜底设计

## 目标与范围

第一阶段建立统一的视觉能力判定层，取代散落的页面特例。转换器继续优先生成可编辑的 FairyGUI 对象；当一个 Figma 节点包含当前无法可靠表达的视觉语义时，只把保持该语义所需的最小节点子树导出成透明 PNG，并停止重复导出其后代。

本阶段覆盖渐变填充、可见阴影与模糊、非 NORMAL 混合模式、多重可见填充或描边、实例、蒙版组和内部裁剪框。九宫格、组件映射、约束布局、完整排版、控制器、列表识别、跨节点资源去重和自动截图差异属于后续独立阶段。

## 方案选择

采用“插件端能力矩阵 + 明确导出策略”。插件拥有 Figma 节点和 `exportAsync`，因此它负责判断 `native / vector_asset / image_asset / composite_png / skip`；Python 生成端只消费已确定的策略，不猜测是否需要重新栅格化。

未采用两种替代方案：全部栅格化会失去可编辑性；让 Python 生成端事后判断则拿不到缺失的像素资源，无法正确兜底。

## 架构

新增独立的 `visual-capability.ts`，输入只读节点快照和“是否顶层根节点”，输出策略、原因代码及目标 MIME。`selection.ts` 使用该结果建立唯一的 DFS 资源计划；任何带资源的节点都是原子子树边界。

判定遵循最小范围原则：子节点自身存在不支持效果时，只栅格该子节点；父节点自身的混合、效果、蒙版或裁剪会改变后代合成结果时，栅格父节点。普通纯色文本、单纯色图形和无效果容器保持原生。顶层普通 `clipsContent` 容器保持可编辑，但内部裁剪容器继续原子导出。

序列化节点在 `properties.export_strategy` 写入稳定策略名，在 `properties.raster_reasons` 写入稳定原因代码。清单增加安全、可读的 `visual_rasterized` 警告，禁止泄露 Figma 原始 ID、路径、URL或图像哈希。

## 能力矩阵

| Figma 特征 | 策略 | 原因代码 |
|---|---|---|
| TEXT + 单个可见纯色填充/描边，无效果 | `native` | 无 |
| FRAME/GROUP + 普通子树，无合成语义 | `native` | 无 |
| VECTOR/BOOLEAN/STAR/LINE/POLYGON/ELLIPSE，无合成语义 | `vector_asset` | 无 |
| IMAGE 填充 | `image_asset` | 无 |
| INSTANCE | `composite_png` | `instance_composite` |
| 含蒙版的 GROUP | `composite_png` | `mask_composite` |
| 非顶层 `clipsContent=true` 容器 | `composite_png` | `clip_composite` |
| 可见渐变填充或描边 | `composite_png` | `gradient_paint` |
| 可见阴影或模糊 | `composite_png` | `visual_effect` |
| 非 `NORMAL/PASS_THROUGH` 混合模式 | `composite_png` | `blend_mode` |
| 多个可见填充或多个可见描边 | `composite_png` | `multiple_paints` |
| VIDEO | `skip` | `unsupported_video` |

不可见 paint/effect 不参与判定。若同一节点命中多个原因，原因去重并按固定优先级排序，确保产物可复现。

## 数据流与错误处理

1. 插件遍历当前选择并为每个节点计算策略。
2. 资源计划按策略声明 SVG 或 PNG；同一引用继续去重。
3. 原子资源节点不再进入其后代，避免双重渲染。
4. 资源导出失败沿用现有 SVG→PNG 降级和安全错误；能力判定本身不访问网络。
5. 生成端保留策略元数据，遇到未知策略按现有兼容路径处理，不令旧清单失效。

## 验收标准

- 每一种矩阵规则都有单元测试，且先验证旧实现失败。
- 不支持效果只栅格最小必要子树，兄弟和无关父级仍可编辑。
- 根节点普通裁剪、原生纯色文本和单纯色图形不会被误栅格化。
- 输出顺序、资源键和原因顺序可复现。
- 插件完整测试、TypeScript 类型检查、Python 完整测试、Ruff 和 mypy 全部通过。
- 用包含渐变、阴影、混合模式、裁剪、蒙版和原生文本的综合夹具验证生成 XML 不重复渲染子树。

## 后续阶段

完成本阶段后，按独立可验收批次依次推进：九宫格；现有 FairyGUI 组件映射；Auto Layout/constraints；完整字体排版；variants/controller；列表识别；跨选择资源去重；转换诊断报告；自动视觉回归。
