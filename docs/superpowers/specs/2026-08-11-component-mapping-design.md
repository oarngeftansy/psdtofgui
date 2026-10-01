# Figma→FairyGUI 组件映射设计

## 目标

将能够可靠识别的 Figma `INSTANCE` 或 `COMPONENT` 映射为现有 FairyGUI 组件引用，减少重复 PNG，同时保证任何不确定情况继续沿用当前原子 PNG 兜底。

## 映射来源与优先级

映射只有两个来源：

1. Figma 图层名称末尾的显式标记 `@fgui(Package/Component)`。
2. 未标记实例名称与 FairyGUI 组件文件名的全工程唯一规范化匹配。

显式标记优先。禁止根据截图、像素、坐标、颜色或 AI 语义猜测组件。

## 名称规范化

自动匹配时，FairyGUI 组件名先移除 `.xml` 后缀；双方再忽略空格、短横线、下划线和大小写。除此之外不删除前缀、不做同义词转换，也不使用模糊距离。

规范化结果必须在全工程中唯一。不同包内出现同一规范化名称属于歧义，不能自动映射。

## 插件端

插件解析可选 `@fgui(Package/Component)` 标记，合法时写入：

```json
{"package":"Base","component":"CommonButton"}
```

字段名为 `properties.fgui_component_ref`。序列化后的显示名称移除技术标记。

格式错误、重复标记或缺少包名/组件名时输出安全诊断并忽略标记。插件仍把实例作为原子 PNG 资源发送，因为插件端不知道上传工程中是否存在目标；生成端成功解析映射后忽略该 PNG，失败时直接使用它兜底。

## 工程索引端

工程索引为 `component` 资源建立：

- 按包名和规范化组件名的精确索引。
- 按规范化组件名的全工程候选集合。

索引同时支持传统 `<package>/package.xml` 和 `assets/<package>/package.xml` 布局。组件必须具有合法 `id`、`name` 和所属 package ID。

## 生成决策

对每个带直接 PNG 资源的 `INSTANCE` 或 `COMPONENT`：

1. 若存在合法显式引用，精确查找包与组件。
2. 否则使用清理后的实例名进行全工程唯一匹配。
3. 找到目标后生成 `<component>` 显示对象，不生成该实例对应的新图片资源。
4. 同包目标只写 `src`；跨包目标同时写 `pkg="目标包ID"`。
5. 保留实例位置、尺寸、旋转和透明度；内部子树仍不展开。
6. 未找到、歧义、目标类型错误或索引异常时保留原子 PNG。

成功映射但同一 PNG 还被其他未映射节点引用时，该 PNG 资源仍必须保留。只有没有任何显示对象再需要它时，才从新资源注册和文件复制计划中移除。

## 诊断

- `component_mapping_explicit`：显式映射成功，INFO。
- `component_mapping_automatic`：唯一名称映射成功，INFO。
- `component_mapping_missing`：显式目标不存在，WARNING，PNG 兜底。
- `component_mapping_ambiguous`：自动匹配不唯一，WARNING，PNG 兜底。
- `component_mapping_invalid`：标记无效或目标不是组件，WARNING，PNG 兜底。

诊断不得包含本地路径、Figma 原始 ID、图片哈希或访问令牌。

## 兼容性与安全

- 旧 version-1 清单没有 `fgui_component_ref` 时仍可自动唯一名称匹配。
- 普通图片、文本和图形不参与组件映射。
- 未映射实例维持第一阶段的 `composite_png` 行为。
- 本阶段不修改现有 FairyGUI 组件 XML，不生成 controller，不写入 Unity 工程。

## 验收标准

- 合法、非法和重复 `@fgui` 标记均有插件测试。
- 自动唯一匹配、显式匹配、跨包匹配、目标缺失和歧义均有生成测试。
- 成功映射输出 FairyGUI `<component>`，不会重复输出对应 `<image>`。
- 映射失败严格回退为 PNG，不令整个工程失败。
- 共享 PNG 仅在没有其他消费者时才从资源计划移除。
- 无映射的现有输出保持不变。
- 插件全套测试、TypeScript、生产构建、Python 全套测试、Ruff 和 mypy 全部通过。
- 生成工程可交由 FairyGUI 6.1.4 检查并继续导出 Unity。
