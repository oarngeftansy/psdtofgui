# Writer 紧凑汇总与富文本原生保留设计

日期：2026-08-24

## 目标

在不启动 Project Binding、不引入页面名或节点 ID 特例的前提下，改进 Writer 的两处通用行为：

1. 自动转换结果使用适合窄屏插件的紧凑清单，不再显示有空白列的大卡片。
2. 富文本按具体字符 run 的能力事实决定转换方式；能由 FairyGUI 6.1.4 已验证方言表达的格式必须保持原生可编辑，不能仅因存在多个 run 就进入图片审核。

## 非目标

- 不控制用户的 Figma 或 FairyGUI Editor。
- 不添加未经 FairyGUI 6.1.4 证据验证的 UBB 标签。
- 不在缺少字符级几何时把一个文本节点猜测性拆成多个文本节点。
- 不自动批准审核、下载 ZIP 或开始 Project Binding。
- 不针对任何业务样例、页面名称或节点 ID 编写分支。

## 自动转换紧凑清单

### 信息结构

自动转换仍按以下通用能力分组：结构与布局、文字、图形、可读实例。每组显示：

- 分类名称与总数，例如 `文字 · 18 项`；
- 节点条目，每行只显示 `名称 · 节点类型`；
- 默认最多显示前 5 项；
- 超过 5 项时显示 `展开其余 N 项`；
- 展开和收起只影响当前分组，展开后按钮显示 `收起`。

删除重复的长说明、大面积卡片背景和无内容的第二列。自动转换、建议审核和阻断项仍保持现有分类边界。

### 可访问性与窄屏行为

- 展开按钮必须具有正确的 `aria-expanded`。
- DOM 顺序与服务端 disposition 顺序一致。
- 名称作为普通文本渲染，不解释为 HTML。
- 单列布局适配当前 360px 宽插件，不增加横向滚动。

## 富文本能力判定

### 已验证原生能力

当所有 run 能由当前 Writer 方言无损编码时，文本直接编译为 FairyGUI `richText`，归入自动转换：

- run 内容按原顺序完整拼接为源文本；
- run 字体候选、字号、描边与基础文本相同或未覆盖；
- run 之间只存在颜色差异；
- 内容不会与当前 UBB color 标记产生歧义；
- 整段字体、字号、颜色、描边和对齐继续使用 typed `TextPlan`。

上述文本不得产生 `rich_text_runs` 审核 disposition，也不得生成 PNG fallback。

### 超出已验证能力

以下差异继续进入 `editable_risk`，但必须给出具体原因，而不是笼统地把所有多 run 文本视为同一问题：

- 逐 run 字体或字重不同；
- 逐 run 字号不同；
- 逐 run 描边颜色或宽度不同；
- run 内容无法安全编码为已验证 UBB；
- 其他未注册 run 样式。

默认处理为保留可编辑文本。栅格化只能作为用户明确选择的策略，且只针对无法表达的最小文本节点，不扩大到父容器。

run 内容与源文本不闭合属于输入事实不完整，必须阻断候选生成；它不能降级为 `editable_risk`，因为系统无法证明连纯文本内容都被完整保留。UBB 歧义则允许以不带逐 run 格式的可编辑纯文本进入 `editable_risk`，但不得声明已原生保留富文本。

### 审核呈现

`editable_risk` 没有真实渲染证据时，不再显示两个空白预览框。界面改为结构化摘要：

- 文本仍可编辑；
- run 数量；
- 已保留的属性；
- 无法等价表达的具体属性；
- 可选处理及其可编辑性影响。

只有取得真实的节点级 Figma 图像和对应 FairyGUI 渲染图时，才显示视觉对比。图片资源的 `raster_preserved` 审核继续使用现有 `sourceNodeId` 证据链。

## 数据流与边界

1. Figma 选择导出保留现有 run 内容与样式事实。
2. UIR 编译验证 run 内容闭包，并记录已注册与未注册样式。
3. Plan capability 根据具体 run 差异选择 `native richText` 或 `editable_risk`。
4. Writer 只序列化已验证的 FairyGUI 6.1.4 富文本编码。
5. 后端 disposition 投影具体风险原因和影响。
6. Writer 面板按 disposition 显示紧凑自动清单或结构化审核摘要。

任何一层缺少必要事实时均失败关闭，不从显示名称推断能力。

## 错误处理

- run 内容无法重组源文本时不得声明无损富文本。
- 未注册样式不得静默丢弃。
- Writer 方言拒绝的富文本不得发布 ZIP。
- 缺少真实预览不是加载失败；界面必须区分“没有生成证据”和“证据请求失败”。

## 测试缝隙

测试只覆盖公开行为和稳定合同：

### Writer 面板

- 每组默认显示 5 项并显示正确余数。
- 各组可独立展开和收起，`aria-expanded` 正确。
- 条目显示名称与节点类型，长说明和空白第二列消失。
- 无真实证据的 `editable_risk` 显示结构化摘要，不渲染空预览对比框。
- `raster_preserved` 仍展示经过 `sourceNodeId` 关联的真实图片证据。

### UIR、Plan 与 Writer

- 正例：仅颜色不同的多个 run 编译为 `richText`，保留内容和颜色，ZIP/XML 验证通过且没有 raster review。
- 反例：逐 run 字号、字体或描边不同，不得伪装为无损 rich text；产生具体 `editable_risk`。
- 反例：run 内容不闭合时阻断候选生成；UBB 歧义时保留可编辑纯文本并产生具体 `editable_risk`，两者均不得发布错误的无损富文本声明。
- 回归：普通纯文本、原生图形、可读实例和既有最小 raster fallback 行为不变。

## 完成条件

- focused 正例、反例全部通过。
- Python 全量、Figma plugin 全量、Web Console 全量、构建/打包 parity、两端 TypeScript、Ruff、strict mypy、Web production build 与 `git diff --check` 全部通过。
- 本地验收插件重建到 `.local-acceptance/plugin/manifest.json`。
- 不宣称 FairyGUI Editor GUI 验收通过，直到用户实际打开 ZIP 并确认。
