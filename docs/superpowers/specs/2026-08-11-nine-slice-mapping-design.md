# Figma→FairyGUI 九宫格映射设计

## 目标

让按钮、面板、输入框等可拉伸图片在 Figma→FairyGUI→Unity 流程中保持角部和边框尺寸，避免整体缩放造成变形。实现必须通用于任意页面，不依赖图层名称、坐标或“村庄升阶”特例。

## 信息来源与优先级

九宫格边界只来自两个确定性来源：

1. 现有 FairyGUI 工程图片资源的 `scale9grid`。
2. Figma 图层名称中的显式标记 `@9s(left,top,right,bottom)`。

现有 FairyGUI 工程优先。如果两个来源都存在且不一致，保留工程值并输出冲突诊断。两个来源都不存在时保持普通图片，不根据透明像素、颜色或内容自动猜测。

## 坐标语义

Figma 标记使用四边不可拉伸边距：`left, top, right, bottom`。四项必须是非负整数。图片宽高为 `W,H` 时，必须满足 `left + right < W` 且 `top + bottom < H`。

FairyGUI `scale9grid` 使用内部可拉伸矩形 `x,y,width,height`，转换公式为：

```text
x = left
y = top
width = W - left - right
height = H - top - bottom
```

生成属性固定写成 `scale9grid="x,y,width,height"`。

## 插件端

新增纯函数解析节点名称中的九宫格标记。合法标记序列化到 `properties.nine_slice_insets`：

```json
{"left":24,"top":24,"right":24,"bottom":24}
```

序列化后的显示名称移除 `@9s(...)`，避免技术标记进入 FairyGUI 对象名。原始节点 ID、路径和图片哈希仍不得进入清单。

以下情况产生安全诊断并忽略标记：格式错误、重复标记、负数、非整数、边距超过当前节点尺寸。无标记不产生诊断。

## 工程索引端

`ProjectResource` 增加可选的结构化九宫格矩形。`project_index.py` 从标准布局和 `assets/<package>` 布局的 `package.xml` 图片节点读取 `scale9grid`，只接受四个非负整数且宽高大于零的值。

损坏的工程九宫格不阻止索引其他资源，但产生可追踪诊断。资源索引保持只读，不修改用户原始工程。

## 资源匹配与生成

现有资源匹配沿用稳定资源身份与名称匹配机制，不新增按坐标或页面名称匹配。生成阶段按以下顺序决策：

1. 若匹配到的现有 FairyGUI 图片带合法九宫格，复用该值。
2. 否则若 Figma 节点带合法 `nine_slice_insets`，根据实际导出图片尺寸换算。
3. 否则生成普通图片。

如果工程值与 Figma 换算值冲突，使用工程值并输出 `nine_slice_conflict`。如果标记在实际导出图片尺寸下越界，生成普通图片并输出 `nine_slice_out_of_bounds`。

新生成图片的 `package.xml` 资源节点携带 `scale9grid`；组件 XML 继续引用同一图片资源，不在显示对象上重复保存九宫格。

## 错误与兼容性

- 旧版 version-1 选择清单没有 `nine_slice_insets` 时行为不变。
- 未知属性继续由现有有界属性管线兼容处理。
- 九宫格错误只降级该图片，不令整个工程生成失败。
- 不改变生产 HTTPS、安全令牌、上传限制或原子子树策略。

## 验收标准

- 合法 `@9s` 标记被解析、移除并安全序列化。
- 格式错误、重复、负数、非整数和节点尺寸越界均有稳定测试。
- 标准与 `assets/<package>` 工程布局都能索引 `scale9grid`。
- 现有工程值优先，冲突时产生诊断。
- 无九宫格信息的普通图片输出完全不变。
- 生成的 `package.xml` 使用正确的 `x,y,width,height`。
- 插件完整测试、TypeScript 类型检查、可复现构建、Python 完整测试、Ruff 和 mypy 全部通过。
- 生成工程可由 FairyGUI 6.1.4 打开，并能继续导出到 Unity。

## 非目标

本阶段不实现自动像素分析、不创建 FairyGUI 组件映射、不生成控制器、不修改 Unity 项目，也不把九宫格信息写入参考 XML 之外的私有格式。
