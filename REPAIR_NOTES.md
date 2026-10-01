# Repair notes — policy revision 25

本次修改针对当前“新资产替换后仍残留旧组件视觉”的根因，不做单页硬编码。

## 已修复

- 将“旧对象保留”拆成“运行时身份保留”和“视觉贡献保留”两个维度。
- 新增 `visual_disposition`：`preserve / retire / other_state / structural`。
- PSD 模式下，如果旧静态 Graph 没有 PSD 对应物，且更高层已映射 Image/Loader/Component 明确覆盖同一区域，则标记 `retire`。
- `retire` 不删除对象、不改 ID、不改类型、不删 Gear/Relation/Transition；只关闭默认目标状态的旧像素。
- PSD 流程停止调用旧的“把旧 Graph 缩到新资源不透明区域下面”的几何补丁。该补丁正是 Noble/Merit 黄色矩形残留的主要诱因之一。
- 新增 `logical_bounds_policy`，Component 默认保留旧逻辑尺寸。PSD visual bounds 不再自动等同于组件逻辑/交互 bounds。
- `_resize_mapped_component_definitions` 仅在显式 `logical_bounds_policy=resize` 时执行。
- policy revision 从 24 升至 25，并用统一常量约束 Store / Workflow / Review。

## 回归

当前容器没有 `psd-tools` 且无网络，无法运行依赖 PSD 解析的完整测试，也无法启动 Windows FairyGUI Editor 6.1.4。

已通过：

- Python `compileall`
- HIFI 核心、不依赖 `psd-tools` 的回归测试：`145 passed`
- 新增 3 个针对本次修复的测试：旧 Graph retirement、Component logical bounds preservation、retire 后对象身份仍保留。

## 仍未解决

Challenge / Noble / Merit 的 PSD 复杂 group/mask/effect 局部渲染忠实度仍受 `psd-tools` 限制。当前修复不会重新使用 merged composite 裁片补像素；需要在有 PSD 渲染依赖和 FairyGUI Editor 的 Windows 环境继续验证组件级 RenderSet 后端。
