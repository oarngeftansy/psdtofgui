# Wiki — 当前项目事实
## 2026-09-28 policy 19：布局序列配对

- auto-layout 组的成员按 displayList 序沿轴排列是运行时权威事实；当某 PSD 组的未认领可见叶子恰好等于条带成员数、类型序列逐位相容、区域在行容差内且候选组唯一时，按秩一一配对（`order_score=1.0`，matched+accept）。类型错位/数量不符/含可见子组/区域过远/成员已决/候选组不唯一均不配对。
- 真实 RAR+PSD 复测（会话 `86f54bb9e75f4b7093ea23dff4f48f28`）：未决 32 → 23；奖励条 n24↔Available Today、n25↔图层 503、n34↔2000 与排名条 n40↔图层 786、n17↔Elf Queen lv.1 全部按秩配对。剩余 23 = fgui_only 3（n33 graph 真实缺口 + 2 个静态 URL icon loader）+ hifi_added 13（2 全画布重复底图 + 6 画布外 + 5 左列装饰，其像素宿主是范围外共享按钮但最小容器是 Tower 底条）+ blocked 5（蒙版）。原始基线 106 → 23。
- 验证：Python `1455 passed, 4 skipped`；ruff 通过；插件 99+tsc、Web 77+tsc 通过。

## 2026-09-28 policy 17/18：范围规则与运行时几何

- 分支 `codex/hifi-replacement-plugin` 提交 `ed6c2e0`（把此前未提交的嵌套/变体/状态视觉/类型授权/像素相似度/描边与效果渲染整套架构连同 policy 17/18 一起落库，69 文件 +8772/−3004）。
- policy 17：`FguiObjectRef` 新增 `relation_side_pairs/auto_layout/layout_gap/layout_excludes_invisible/position_runtime_bound`；带 relation 或处于 advanced auto-layout 组的对象 XML `xy` 非运行时权威，评分剔除位置项并按 0.76 归一。空 target 的父锚定 relation 不再被静默丢弃。
- policy 18：跨包（`pkg` != 目标包）实例子树标记 `out_of_scope`，映射状态新增 `out_of_scope`（action=preserve_structure，不计未决）；PSD 叶层最小可见旧容器为范围外对象时同样出队；`require_psd_coverage` 接受范围决定；generated-state 匹配优先于范围排除。auto-layout 成员在组原点权威时获得计算出的运行时坐标（hz: 组原点+累计宽度+colGap，excludeInvisibles 时不可见成员不占位；真实工程 n46 逐像素吻合）。owned 提升门槛按 0.76 缩放、锚点从 `candidates[0]` 恢复、保留文字可与唯一同父旧文字配对。
- 真实 RAR+PSD（隔离副本实测，`%TEMP%\hifi18-data`）：未决 106 → 32（fgui_only 8 / hifi_added 19 / blocked 5）；Delegate/Reward 两组 owned 归属恢复（含两标题配对）；RedDot 108 条状态匹配保留；out_of_scope 共 263 项、待处理 0。剩余 32 条构成：画布外 6、组 77 2、纯视觉多叶 6、hz 条带成员 5（含 2 静态 URL loader）、Elf Queen/图层 786 2、蒙版 5、图标 loader 2。
- 验证：Python 全量 `1448 passed, 4 skipped`；ruff 通过；插件 99+tsc、Web 77+tsc 通过。mypy 仍被 numpy 2.5.3 stub（PEP 695 语法）卡死，`--python-version 3.12` 可运行并暴露 99 个未提交模块的真实类型错误（psd_effect_render 23/hifi_mapping 20/hifi_nested 18 等）；venv numpy 2.5.3 与 portable-constraints 2.4.6 已漂移；`pyproject.toml` 已补声明 aggdraw/numpy/scikit-image 运行时依赖。

## 2026-09-24 PSD 最小复合组皮肤与可编辑文字挖空

- PSD 顶层组只有在范围小、视觉密集且确实依赖组级混合时才允许成为局部复合皮肤；高度超过文档 25%、横跨大区域、简单可编辑横幅和低于 48px 的密集文字条继续逐层转换，避免把动态界面或相邻区域烘成死图。
- 选中的复合组使用 PSD 内嵌 merged composite 保留准确颜色和背景混合，再按 HIFI-IR 中已解析的文字边界生成完整遮罩；不依赖 psd-tools 的可选矢量合成器。局部皮肤挖掉文字，PSD TEXT 继续作为可编辑对象并统一排在皮肤之上。局部皮肤保持 `touchable=false`，不会改变旧对象的输入行为。
- 真实主页当前基线会话 `2012ca23085a4c9e821a90595fac5a69`、候选 `db8af11e75e1a65514198c284f10fcaa3b5988c712e479979716c039959f5c9f` 使用 2 个最小复合组皮肤，产生 48 个文件差异和 96 个对象差异。FairyGUI 6.1.4 获取 1080×1920 完整渲染，旧对象结构和资源闭包通过，平均像素差为 0.015236。Challenge 的双层文字残影已消失；位于超高纵向祖先组内的 Record/Rank/Shop 等动态导航子组不会整体烘焙。
- 候选继续保持不可批准：FairyGUI 与 PSD 的文字度量、字距、描边和抗锯齿仍不完全一致；Controller、按钮状态、Transition 与动画关键帧也仍需逐状态 Editor 渲染证据。

## 2026-09-24 PSD 逐层候选替代整屏保真覆盖

- PSD 候选不再写入 `HIFI_PSD_Parity` 整屏图层；PSD 合成图只作为 Editor 像素审核基准。真实候选因此能暴露逐层转换缺口，也不会用静态截图遮住旧组件的动态视觉。
- 映射器现在允许有面积的 PSD `IMAGE` 叶子进入 `add_visual`，0×0 空像素层继续作为例外；主页新增 6 个有效像素/智能对象视觉层，所有新增皮肤层均写入 `touchable=false`。
- PSD 节点携带全局 `psdDocumentIndex`。已映射旧对象与新增视觉层按原 PSD 顺序重排；旧对象 ID、类型和程序子节点不变。映射到非 image 旧对象的栅格资源以独立皮肤图覆盖，不把 GGraph/GComponent 改成 GImage。
- 真实主页候选会话 `43507181afaf48c898830d3730afbc20` 已生成无整屏覆盖候选 `366e8d95dfe8d249191957649da1d0b644089e652093c9411fb2909c4329a655`，70 个文件差异、129 个对象差异。FairyGUI 6.1.4 完整截图为 1080×1920，平均像素差从逐层初版的 0.5809 降至 0.0207；候选保持不可批准。剩余主要缺口是复杂按钮组的组级混合/效果，以及 Controller/Transition 状态和动画关键帧的视觉证据。

## 2026-08-27 内网 SVN 源码镜像

- 当前项目已从 Git 提交 `e33869b11cc67eb8448aaecd408ccff9a087d931` 生成干净 archive，并首次导入
  `内网 SVN 仓库（地址已移除）`；初始导入 revision 为 `r131`。
- 快照含 354 个受 Git 跟踪文件；未包含 `.git`、`.local-run`、`.local-acceptance`、`node_modules`、
  pytest 临时目录、token 或用户生成数据。远端已核对 `Start-Local.ps1` 和本机运行说明存在。
- 本机 SVN CLI 为 `C:\Program Files\SlikSvn\bin\svn.exe` 1.14.2。仓库证书当前需显式接受内网
  unknown-CA/CN mismatch；认证使用机器现有 SVN 凭据缓存，部署命令不保存或输出密码。

## 2026-08-26 Git clone 后 Windows 本机一键运行

- 仓库根新增 `启动本机版.cmd` / `Start-Local.ps1`。同事把私有仓库 clone 到
  `C:\src\figma-to-fgui` 后运行该入口，首次自动检查或通过 winget 安装 Python 3.11 与 Node.js LTS，
  创建 `.local-run/venv`、安装锁定前端依赖、构建 Web Console 和绑定
  `http://localhost:8765` 的本机 Figma 插件，然后只在 `127.0.0.1:8765` 启动 Writer。
- 每台电脑独立生成随机插件 token，token、生成插件、虚拟环境和 Writer 数据均位于被 Git 忽略的
  `.local-run/`，不会提交或在同事之间共享。首次导入路径固定为
  `.local-run/plugin/manifest.json`；此路径不使用 Cloudflare、内网 IP、Project Binding 或公网部署。
- 使用说明位于 `docs/deployment/git-clone-local-windows.md`。脚本明确拒绝非 ASCII checkout，避免已知的
  Windows pnpm/esbuild 路径故障；依赖初装需要访问 Python 和 npm 依赖源。
- 验证：启动器正反例 `3 passed`；Python 全量 `1250 passed, 4 skipped`；Figma plugin
  `242 passed`、build/package `5 passed`；Web Console `56 passed`；两端 TypeScript、Web/plugin build、
  Ruff 和 strict mypy（61 source files）通过。沙箱禁止联网，因此全新 venv 的在线依赖下载只能验证
  为明确失败，真实首次在线安装需在同事电脑执行。

## 2026-08-26 隐藏节点保留正常转换与闭眼状态

- 最新 selection 有 10 个 `visible=false` 节点，其中隐藏 INSTANCE/VECTOR 因位于父 clip 外被提前改成
  `native`、0 children、0 resources，后端再用“隐藏吸收能力”兜底为空 container；虽然不可见事实存在，
  原类型和资源映射已经丢失。
- 源节点本来隐藏时现在不参与可见区域 clip-out/fragment 优化，仍按原类型执行完整转换：VECTOR 保留
  vector PNG，opaque INSTANCE 保留 composite PNG，可读容器保留 children/bounds。最终只在 Plan/XML
  写入 `visible=false`，FairyGUI Editor 对应闭眼状态。
- 后端删除隐藏叶节点自动变空 container 的兜底；隐藏节点缺少正常资源/能力时必须阻断，不能静默输出
  假对象。正例闭合验证 hidden INSTANCE 仍为 image、资源存在、Plan visible false、ZIP XML 含
  `visible="false"`；反例验证删掉资源后不生成 ZIP。
- focused Python `245 passed`，隐藏正反例 `2 passed`；Figma plugin `233 passed`、build/package `5/5`，
  Python 全量 `1247 passed, 4 skipped`；TypeScript、Ruff、strict mypy 与 diff check 通过。本地插件已重建，
  后端需从本提交重启后再生成新候选。

## 2026-08-26 纯视觉简单遮罩按最小子组转 PNG

- 嵌套 mask 不能一律丢弃关系：类似“Mask 图片 + 矩形/图形”的纯视觉子组若拆成独立对象，会在
  FairyGUI 中暴露原本只用于遮罩的绿色方块。现在没有任何 TEXT 后代的嵌套 mask Group 作为最小
  视觉单元输出一张 `mask_composite` PNG。
- 外层包含文字、数值、按钮等可编辑后代的卡片 Group 继续保留结构，并递归寻找内部最小纯视觉
  mask Group；不会重新退化为整张卡片 PNG。Group 自身的 effect/blend 等独立原因仍按原规则处理。
- 回归锁定外层 Editable card/native、数值 TEXT/native、内部 Check mask/composite_png 且仅一项资源。
  selection focused `41 passed`，Figma plugin `232 passed`，build/package `5/5`，Python
  `1246 passed, 4 skipped`；TypeScript、Ruff、strict mypy 与 diff check 通过。本地插件已重建。

## 2026-08-26 跨裁切边界的结构 Group 递归最小化

- 四个卡片 Group 除嵌套 mask 外还相对父 clip Frame 上移 `19/10px`；selection 的 `clipFragment`
  优先分支因此继续把整个直接子 Group 标为 `composite_png/mask_composite`。只修 mask classifier 不会
  命中这条更高优先级路径。
- 新规则对跨边界、含 children、且自身仍为 native 的 GROUP 保留完整 bounds/层级并把父 clip 继续
  递归传给 children；完全在内的文字继续 native，只有真正跨边界的最小叶节点使用交集 bounds 导出
  PNG。无 children 的跨界叶节点仍按既有规则裁图；Group 自身有独立 effect 等 raster 原因时也不绕过。
- 回归直接锁定 viewport `0..320`、Group `260..360`：Group 保留 100px 与 children，内部 label native，
  artwork `300..350` 仅导出可见 `300..320` 的 20px fragment。selection focused `40 passed`，插件
  `231 passed`，build/package `5/5`，Python `1246 passed, 4 skipped`；TypeScript、Ruff、strict
  mypy 与 diff check 通过。本地插件已重建。服务 selection 最新时间仍为 11:43:58，说明在本轮完成前
  没有新 manifest 上传；旧 ZIP/已导入工程不会自动改变。

## 2026-08-26 复杂嵌套蒙版组不再整组压图

- 首轮嵌套蒙版修复只覆盖 `nativeMaskDescriptor` 可识别的简单矩形，真实四个 Group 仍在插件 selection
  阶段以 `mask_composite` 整组压成 asset-36..39，children 在上传前已清空；后端无法恢复。最新规则对
  所有嵌套含 mask 子节点的容器单独移除 `mask_composite` 整组理由，保留容器与完整子树。
- 该放宽只吸收“嵌套 mask 关系不可编码”这一项：mask 子节点自身的渐变等能力仍在最小节点级降级；
  Group 自身若还有 `visual_effect/blend_mode` 等独立不可表达原因，仍按剩余原因整组保真，不会静默漏效果。
  根级 mask 与跨 clip 边界 fragment 的既有严格路径不变。
- 正例覆盖复杂 luminance/gradient mask 下文字仍可编辑，反例覆盖带 DROP_SHADOW 的 mask Group 仍按
  `visual_effect` 失败关闭。selection focused `39 passed`，Figma plugin `230 passed`，build/package
  parity `5/5`，Python `1246 passed, 4 skipped`；TypeScript、Ruff、strict mypy 与 diff check 通过。
  本地插件已重建，旧 selection/候选已经剪掉 children，必须重新加载插件并重新生成才能验证新层级。

## 2026-08-26 建议审核步骤分类口径修复

- ReviewPanel 一直会展示 `raster_preserved`，但 Writer 是否进入“建议审核”的计数过去只包含
  `editable_risk/blocked`；当候选只剩图片保真项时，界面因此错误直达“确认下载”。现在步骤门与
  展示列表统一使用 `raster_preserved/editable_risk/blocked`，只有真正全 native 的候选才能跳过审核。
- 新增 raster-only 正例，锁定按钮必须为“查看建议审核”、必须显示图片保真项且不能提前出现下载按钮；
  native-only 反例继续允许直接进入最终检查。Web focused `31 passed`、全量 `56 passed`，Figma plugin
  `228 passed`、build/package parity `5/5`、Python `1246 passed, 4 skipped`，Web build/两端类型检查、
  Ruff、strict mypy 与 diff check 通过。本地验收插件已重建，须重新加载插件 UI。

## 2026-08-26 嵌套矩形蒙版组优先保留可编辑层级

- 插件过去会把所有可识别的矩形蒙版组在非选择根位置强制标为 `composite_png`。这导致普通子 Frame
  虽已保留，其内部每个带矩形蒙版的 Group 仍被整组压成图片，文字和状态图层全部失去编辑能力。
- FairyGUI 6.1.4 在单一组件 display tree 内没有经过验证的嵌套 Group-mask 编码；本轮通用规则改为
  保留嵌套 Group 及完整子树，并只降级无法编码的嵌套蒙版关系，不再整组栅格化，也不生成额外
  `Clip_*` 内部组件。选择根上的受支持蒙版继续使用既有原生 mask 编码；复杂/不受支持蒙版仍按
  `mask_composite` 最小图片保真，未放宽安全边界。
- 正反例和全量门：selection focused `37 passed`，Figma plugin `228 passed`，build/package parity
  `5/5`，Python `1246 passed, 4 skipped`；插件 TypeScript、Ruff、strict mypy（61 source files）及
  `git diff --check` 通过。`.local-acceptance/plugin` 已重建；此项是插件 selection 逻辑变更，必须
  重新加载插件并重新生成 selection，旧候选中的四张 Group PNG 不会自动恢复层级。

## 2026-08-26 子 Frame 层级与越界裁切收口

- `clipsContent=true` 的子 Frame 继续作为普通可编辑容器留在唯一主组件的 display tree 内；完全在裁切边界内的子树保留原生层级，跨越边界的最小直接子树由插件临时克隆到矩形裁切 Frame，只导出可见交集 PNG；完全在边界外的子树保持不可见且不导出资源。该规则不依赖页面名、节点 ID 或业务样例。
- 无 fill/line 视觉属性的 clip Frame 不再额外输出一个同名 background graph，因此 Editor 中不会同时出现“Frame 20 组 + Frame 20 图形”；真实有填充或描边的 Frame 背景仍保留可编辑 graph。
- 本轮沙箱门：裁切正例/反例与插件 bridge `70 passed`，Figma plugin 全量 `227 passed`，build/package parity `5 passed`，Python 全量 `1236 passed, 4 skipped`；TypeScript、Ruff 与 strict mypy（61 个源文件）通过。本地验收插件已重建，8765 服务已从当前分支重启为 PID `23732`。真实 Editor 视觉仍需用户重新加载插件、重新生成新候选后验收；旧候选不包含新的裁切片段事实。

## 2026-08-26 文本框自动尺寸语义

- Writer 不再把所有 Figma TEXT 强制为 FairyGUI `autoSize="none"`。`WIDTH_AND_HEIGHT` 保留为 `autoSize="both"`，避免紧贴字形的文本框因 FairyGUI 字体度量差异被截字；`HEIGHT` 保留为 `height`，固定宽度仍不变；`NONE` 和 `TRUNCATE` 继续为 `none`。该事实已从 selection 现有 `textAutoResize` 贯穿 UIR、Plan 与 Editor XML，无文案/节点特例。
- 未提供 `textAutoResize` 的旧 UIR/Plan 不序列化 null 新字段，因此旧 golden 与稳定身份不发生无意义变化。正例、反例和全量门为 `1240 passed, 4 skipped`，Ruff、strict mypy（61 个源文件）与 `git diff --check` 通过。8765 服务已从当前分支重启为 PID `39228`。

## 2026-08-26 旋转位图与 PNG 单次变换

- Figma `exportAsync(PNG)` 输出已经烘焙节点角度的轴对齐渲染图。过去只对 vector PNG 清零 rotation，旋转位图和 composite PNG 仍把源角度交给 FairyGUI，会发生二次旋转，同时围绕错误的轴对齐框再布局，造成角度与位置一起偏移。现在所有插件实际导出的 PNG（vector/image/composite）统一使用 `absoluteRenderBounds` 并设 rotation=0；后端拒绝仍携带非零 rotation 的导出 PNG manifest。
- 正反例与全量门：Figma plugin `228 passed`，build/package parity `5 passed`，Python `1242 passed, 4 skipped`，Ruff、strict mypy（61 个源文件）、TypeScript 与 diff check 通过。`.local-acceptance/plugin` 已重建，8765 服务已重启为 PID `36712`。必须重新加载插件并生成新候选，旧 selection/ZIP 仍包含二次旋转事实。

## 2026-08-24 Writer 原生可编辑与审核证据收口

- 新建工程管线已从“视觉保真优先的泛化图片 fallback”收紧为“FairyGUI 原生可编辑优先”：Plan schema v2 新增 typed `GRAPH` / `GraphPlan`，Writer 可输出原生 `<graph>`，覆盖纯色矩形、椭圆、描边、统一圆角、根 Frame 背景及根裁切。纯文本保持文本层，可读实例保留子层级；没有为村庄升阶、页面名或节点 ID 写特例。
- 插件只把 FairyGUI 确实无法可靠原生表达的最小子树列为 raster review，例如复杂渐变/effect/blend、特殊 mask、不可表达 transform、复杂多样式文本和不可读实例。可原生处理的结构、文字、图形、组件会自动转换并分类汇总，不再制造几十张同话术审核卡。
- 审核项现在分别提供具体的检测结果、处理方式和可编辑性影响，并按 `blocked → editable risk → raster` 排序。真实预览通过稳定的 `source_node_id` / `sourceNodeId` 与审核项关联，不依赖数组位置或显示名称；无真实证据时明确显示不可预览，不再复用占位图冒充 Figma/FairyGUI 对比。
- Writer archive 的 source、Plan、manifest、XML graph 已建立一致性校验；根背景不会再消失。插件 iframe 增加 FileReader 预览 fallback，竖屏审核布局放大重点内容且避免底栏覆盖。
- 插件构建在普通仓库继续使用 esbuild；仅在 Windows `.worktrees` 的 pnpm junction 受限场景自动使用 Vite fallback，package parity 仍验证 checked-in dist 与 fresh build 一致。
- 本地验收插件位于 `.local-acceptance/plugin/manifest.json`，plugin ID 为 `123456789`，服务地址为 `http://localhost:8765`。该目录只用于本机验收，正式分发仍须替换为真实 HTTPS 服务地址、原插件数字 ID 和部署 token。
- 最新沙箱自检：Python 全量 `1161 passed, 4 skipped, 3 warnings`（warning 为既有 Pydantic negative model-copy serializer warning）；Figma plugin `200 passed`；Web Console `42 passed`；插件 build/package `5 passed`；两端 TypeScript、Web production build、Ruff、strict mypy（60 个源文件）及 `git diff --check` 全部通过。自检没有控制用户电脑或 Figma。

### 下一步验收与修复顺序

1. 用户在 Figma 中重新加载 `.local-acceptance/plugin/manifest.json`，确认本地服务运行于 `8765`，用多份通用竖屏画板验收：至少覆盖纯色图形、纯文本、可读实例，以及渐变、阴影、mask 等审核候选。
2. 每份画板先检查“自动转换”分组是否完整保留可编辑内容，再只审核真正需要图片 fallback 的最小局部；逐项核对 Figma 原图与 FairyGUI 结果是否为该节点的真实证据。
3. 下载 ZIP 并在 FairyGUI Editor 6.1.4 打开，检查组件层级、文字/图形可编辑性、根背景、资源数量和最终画面。村庄升阶只能作为其中一份回归样例。
4. 若仍有问题，记录具体 Figma 节点类型/样式、`sourceNodeId`、审核 reason、服务端 public diagnostic 和生成 XML/ZIP；修复通用能力规则，并新增该规则的正例与反例后再跑全量门。
5. 未获得明确新授权前不要开始 Project Binding。验收通过后，才用真实 HTTPS 地址、原插件 ID 和正式 token 构建可分发插件。

### 当前已知边界

- 尚无经过确认的 FairyGUI 原生编码时，嵌套 clip/mask、复杂渐变/effect/blend、特殊 transform 和复杂多样式文本仍会进入最小局部审核；这不是把整个页面转图片。
- 可复用 FairyGUI component reference 需要完整 definition tree；需求方通用 mapping catalog 只有逻辑语义，不足以凭名称生成真实组件引用。
- 本轮只完成沙箱逻辑、构建和产物闭包验证；按用户要求没有自动操控其 Figma/FairyGUI 桌面，因此真实 GUI 打开与视觉验收仍由下一步用户执行。
- 2026-08-24 续接验收曾发现 `8765` 旧进程未加载最新分支代码：候选 `1684e2036c8f4d23bc98eff8bdc05d82` 的 HTTP review 缺少严格合同必需的 `source_node_id`。当前源码后端审核投影 `4 passed`，插件合同含缺字段反例 `70 passed`，确认不是 `563bcbb` 源码回归。经用户明确授权后已停止旧 PID，并从当前工作树重启服务；新服务现监听 `127.0.0.1:8765`。启动恢复门将旧活动候选闭合为 `new_project_state_conflict`，不得复用，下一步须由用户在 Figma 插件中刷新选择并重新生成候选，再继续审核、ZIP 下载与 Editor 验收。
- 2026-08-24 通用重试继续发现并修复三条管线边界：有完整 committed child tree 的 `INSTANCE` 在无 verified definition 时按原生容器内联，只有不可读实例继续 `component_definition_missing`；不可见 fill/stroke/effect 不再误触发 visual-style 阻断，真实可见且不可表达的样式仍失败关闭；Figma `LEFT/CENTER/RIGHT/JUSTIFIED` 与垂直 `CENTER` 在 Plan 边界规范化为 FairyGUI 6.1.4 对齐方言。无页面名、节点 ID 或业务样例特例。
- 修复后公开接口第 3 代候选 `ca13d1f2268042db901786e9c1bd0605` 已为 `awaiting_review`、`artifact_ready=true`、`approvable=true`，产物 `3-FairyGUI.zip` 为 `1780644` bytes，SHA-256 `505d023f47213dc44400798ed17d0865edac322adde423957b2f12d5e750f8ab`。审核汇总为 `native 71`、`raster_preserved 21`、`editable_risk 1`、`blocked 0`；不得代替用户批准或在批准前下载。当前服务实际监听进程 PID `29156`。
- 本轮最终沙箱自检：通用正反例 `4 passed`，Writer/Plan/UIR 相关 `231 passed`，Python 全量 `1164 passed, 4 skipped, 3 warnings`；Figma plugin `200 passed`、build/package `5 passed`，Web Console `42 passed`；Ruff、strict mypy（60 个源文件）、两端 TypeScript、Web production build 与 `git diff --check` 全部通过。未控制用户电脑、Figma 或 FairyGUI Editor。
- 2026-08-24 Figma 实际重试日志证明 selection manifest、37 个资源与 commit 全部成功，最终 `POST .../new-fgui-projects` 因工程名合同返回 `422`，但旧 UI 只显示笼统“生成失败”。Writer 面板与公共客户端现使用同一闭合工程名规则，在任何选择/资源上传前拒绝空格或非法符号；服务端 validation 会显示“创建候选 · validation”、字段级修复提示及不含 token/节点内容的“复制诊断信息”。已重建用户实际导入的 `.local-acceptance/plugin/manifest.json`，原路径和 plugin ID 不变。
- 诊断 UI 修复最终自检：TDD 正例/反例 `4 passed`；Figma plugin 全量 `201 passed`，Web Console `44 passed`，build/package `5 passed`，两端 TypeScript 与 Web production build 通过；Python 全量短路径复跑 `1164 passed, 4 skipped, 3 warnings`。一次并行 Web 端口测试受沙箱 `EACCES`、一次 Python 长路径/异步批次出现 3 个环境失败，均在隔离短路径重跑通过，随后全量门通过。
- 2026-08-24 相似能力收口不再把“能生成”误写成“原生可编辑”：任意 VECTOR 的 PNG 降级明确归为 `raster_preserved/rasterized_vector`；图片资源归为 `native_image`；可读 INSTANCE 仅声明原生内联结构（`native_instance_structure`），不冒充可复用 FairyGUI component。基础文本的 line-height、letter-spacing、auto-resize 会进入结构化 editable risk，不再静默丢失。
- 嵌套 FRAME/COMPONENT 的受支持纯色背景现以原生 container + background graph 输出；非统一四角圆角写入 FairyGUI `_quad`，完全透明 paint 不再误判为多重填充。自动转换汇总保持每组默认 5 项、独立展开和窄屏自适应，普通项只列类型与对象，不再留空的右侧预览区。
- mask/resource 阻断仍保持 fail closed：失败构建阶段目前没有经过验证的 source-node 映射，禁止按名称、顺序或节点 ID 猜关联。只有建立稳定来源闭包后才能给这类阻断项增加可定位证据。
- 本批最终沙箱门：Python 全量 `1208 passed, 4 skipped`；Figma plugin `213 passed`、Web Console `54 passed`、插件 build/package parity `5/5`；Ruff、strict mypy（60 个源文件）、两端 TypeScript、Web production build 与 `git diff --check` 通过。Python 必须使用短 ASCII `basetemp`，工作树中文路径会造成 Windows ZIP/指纹测试环境误报。真实 FairyGUI Editor GUI 验收仍待用户执行。
- 随后的真实选择重试定位到资源闭包失败：插件仍为仅含 `rich_text_runs` 的 TEXT 上传 PNG，而 Plan 已按 editable risk 保留文本，造成 uploaded 36 / used 35。通用修复是在插件能力边界将“仅富文本 runs 风险”保持为 native + risk facts，不生成无用 PNG；其他视觉/transform 原因仍栅格化，严格资源集合校验没有放宽。修复后插件 `214 passed`、Web `54 passed`、Python `1208 passed, 4 skipped`、package parity `5/5`。
- 2026-08-25 真实候选的 41 张审核卡中，19 张为 editable text risk；其中 18 张具有完全相同的 `line_height,text_auto_resize` 风险签名，另 1 张增加 `text_run_fill`。审核 UI 现按 reason、保留/不支持属性、run 数、转换策略和三类影响的完整签名合并 editable-risk 卡；raster/blocked 仍逐项显示。同类卡显示覆盖数量、代表名称并只确认一次，不提供会误导为批量操作的单节点 adjustment。该样例预期 41 → 24 组。最终门：Web `55 passed`、plugin `214 passed`、Python `1208 passed, 4 skipped`、package parity `5/5`，两端 TypeScript 与 Web build 通过。
- 2026-08-25 矢量能力边界已收口：插件对任意 VECTOR 优先导出 SVG，只有 Figma SVG export 实际失败时才回退 PNG；Writer 接受尺寸/声明一致、无脚本/事件/外链/DOCTYPE/entity/CSS URL/动画等主动内容的自包含 SVG，并在 ZIP 中原样保存为 FairyGUI 图片资源。简单矩形、椭圆等仍优先原生 Graph，可编辑图形属性；复杂路径保留无损缩放和资源替换能力，但不宣称 FairyGUI 可编辑路径锚点。审核新增自动项 `native_vector_resource`，PNG 回退仍明确标为 `rasterized_vector`，无页面名、节点 ID 或业务样例特例。
- SVG 收口验证：资源门正反例、公开 selection→Writer→ZIP→reopen、XML/file closure 等 focused `214 passed, 1 skipped`；Python 全量 `1211 passed, 4 skipped`；Figma plugin `215 passed`；Web Console `55 passed`；package parity `5/5`；Ruff、strict mypy（61 个源码文件）、两端 TypeScript 与 Web production build 通过。当前分支服务已用明确 Python runtime 重启为 PID `17336`；真实 HTTP 上传安全 SVG 后候选为 `awaiting_review`、`approvable=true`、disposition `native:native_vector_resource`、editability impact `vector_path_not_editable`，随后已拒绝清理。真实 FairyGUI Editor 6.1.4 打开/发布仍须由用户执行，不能由沙箱结果代替。
- 2026-08-25 真实选择在 SVG 版本重试时出现统一 `fgui.writer.workflow.validation_failed`。原样重放定位并修复四个通用缺口：Unicode 图层名中的内部 `/` 不再误判为 POSIX 绝对路径；不可见 effect 不再阻止纯色圆角矩形转原生 Graph；已明确 raster fallback 的节点可吸收其 Auto Layout 渲染，普通 Auto Layout 则按已解析绝对几何保留原生容器/子对象并归类为自动项 `native_static_layout`（只是不迁移自动重排规则）；native mask/clip 可包含非阻断 editable-text risk，根 mask scope 只要求覆盖直接显示子对象，不重复要求所有后代。
- 同一 committed selection 的反馈环最终生成 1 根、86 对象、37/37 资源闭合 ZIP。最终门：受影响切片 `492 passed, 1 skipped`，Python 全量 `1219 passed, 4 skipped`，Figma plugin `216 passed`，Web `55 passed`，package parity `5/5`，Ruff、strict mypy（61 个源码文件）、两端 TypeScript 与 Web build 通过。服务已重启为 PID `19760`；同一真实 selection 的新 HTTP 候选 `bb6a3750e65a4cd2af44c78fe1c0981b` 达到 `awaiting_review`、`artifact_ready=true`、`approvable=true`、739679 bytes，随后已拒绝清理。审核原始 dispositions 为 native 58、editable risk 14、raster 17，其中静态布局自动项 2；真实 Editor 验收仍待用户执行。
- 2026-08-25 后续真实重试的统一 `The new-project writer could not produce a validated archive.` 定位为安全 SVG 资源的计划尺寸空缺：资源自身明确为 `82×166`，但来源节点 bounds 为零，导致 Writer 的 SVG 元数据门拒绝。selection normalize 现只对已通过同一安全 SVG 门且具有正整数固有宽高的资源回填尺寸；非法、外链或无确定尺寸 SVG 仍失败关闭。原样重放最新 selection 后得到 1 根、93 对象、35/35 资源闭合 ZIP（1776288 bytes）。最终门：Python `1220 passed, 4 skipped`，plugin `216 passed`，Web `55 passed`，package parity `5/5`，Ruff、strict mypy（61 个源码文件）、两端 TypeScript 与 Web build 通过。
- 2026-08-25 FairyGUI Editor 树验收发现 Writer 虽保留对象/group 关系，却把根组件、显示对象和资源的可见 `name` 写成内部哈希。实时 selection workflow 现以独立 source-name sidecar 将 Figma 图层名安全投影到 Manifest/XML；canonical Plan、标准 CLI Writer 和既有 Editor golden 不变。非法文件名字符确定性替换，组件/资源或重复资源同名时资源追加 `_resource`/稳定序号消歧。真实 selection 重放产出单一主组件 `村庄升阶-3c2be1d9.xml`，92 个显示对象均为可读图层名，8 位哈希图层名为 0；35 个 resources 仅作为该组件的图片/SVG/审核后最小栅格依赖。最终 Python 全量 `1221 passed, 4 skipped`，Ruff、strict mypy（61 个源码文件）与 `git diff --check` 通过。
- 2026-08-25 嵌套原生裁切的真实重试暴露了三层通用闭包：嵌套 native clip 作为外层 clip 内容时仍须保留自身 `native.clip_source` 角色；可读 `INSTANCE clipsContent=true` 是合法矩形容器裁切；vector PNG 外层的 `TRANSFORM_GROUP` 应作为保留坐标/旋转的原生容器。Writer 将内层 clip 提升为内部组件时，还必须把外层 mask 的 content UIR ref 改指生成的 component reference，否则 Plan 提升后会出现 `mask_hierarchy_incoherent`。
- 同一份刚失败的 committed selection 原样重放现为 1 根、163 个 Plan 对象、64/64 资源闭包，完整 Writer/ZIP 构建成功（971443 bytes，18 条均为非阻断 warning）。真实 HTTP 首轮又暴露 Windows 深层 artifacts 临时展开路径达到 265 字符；工程树现先在系统短临时根展开和验证，ZIP 再暂存回目标目录并同目录原子发布，可读 Figma 名称不截断。正例覆盖嵌套 clip、Instance clip、Transform Group、短临时根，反例继续锁定显式 mask 与 clipsContent 组合及临时目录清理失败必须 fail closed。最终门：Python `1235 passed, 4 skipped, 3 warnings`，Figma plugin `223 passed`，Ruff 与 strict mypy（61 个源码文件）通过；真实 FairyGUI Editor GUI 打开仍待用户执行。
- 后续插件显示“ZIP 校验失败 / invalid_response”并非 ZIP 损坏：候选 `01f62bb1a1c746a28372f24cea81de9d` 实际为 `awaiting_review`、`artifact_ready=true`、970565 bytes、SHA-256 闭合，且尚未调用 download。审核响应新增 3 个通用 `TRANSFORM_GROUP/native_structure` disposition，但插件严格 source-type 白名单未同步，因而在 review 解析阶段拒绝合法响应。客户端合同现接受 `TRANSFORM_GROUP`，仍拒绝未知类型；笼统错误文案改为“服务返回的数据无法识别”，不再冒充 ZIP 校验。实际 `.local-acceptance/plugin` 已重建。最终门：plugin `224 passed`、package parity `5/5`、Web `55 passed`，两端 TypeScript 与 Web build 通过。
- 该候选进入建议审核后“确认审核结果”仍禁用，原因不是用户漏勾选：5 个 Writer 生成的嵌套 clip 内部组件在 review 投影时用原始 Plan 查 synthetic definition root，查不到便把整组件误标为 `geometry_valid=false/text_valid=false`，令 `approvable=false`。review 现对“manifest 中新增且原 Plan 无对应 definition”的根对象验证其无父级、bounds 等于组件 size、text 为空；其余对象仍逐项与原 Plan transform/text 严格相等，伪造根几何反例继续阻断。Python 全量 `1235 passed, 4 skipped, 3 warnings`，focused 正反例、Ruff、strict mypy 通过。

## 2026-08-21 Writer capability review alignment

- Writer 的能力判定现由后端单一 `NewProjectConversionDisposition` 合同负责，闭合等级为
  `native`、`raster_preserved`、`editable_risk`、`blocked`。渐变、复杂阴影、遮罩、实例、
  普通视觉样式、不可原生表达的变换和复杂文本在存在受控 PNG fallback 时不再直接导致通用
  validation failure；审核明确区分画面保真与可编辑性影响。
- 插件审核先显示“自动转换 / 建议审核 / 必须处理”三类汇总；选择阶段重复的 fallback warning
  已合并为一条摘要。只有红色 blocked disposition 阻止批准。分析被阻断时仍返回可定位、可拒绝的
  review，但 `artifactReady=false`，不得批准、预览生成资源或下载 ZIP。
- 本轮无 Project Binding、无村庄/页面名/节点 ID 特例。村庄升阶仍仅是通用回归输入。
- 自动化门：Python 全量 `1154 passed, 4 skipped`；Web Console `35 passed`、typecheck/build 通过；
  Figma plugin focused/full 与 package parity 已验证（一次全量 harness 5 秒超时，单独立即重跑
  `7 passed`）；Ruff 和 strict mypy 59 个源文件通过。
- 本地验收插件已重建到
  `.local-acceptance/plugin-current-http/manifest.json`（plugin ID `987654321012348`），服务为
  `http://localhost:8765`，当前后台 PID `31504`。正式分发仍需用真实 HTTPS 服务地址、原插件 ID
  和部署 token 重新构建；localhost 目录只用于本机验收。
- 360×680 插件布局只保留 `.writer-shell` 一条纵向滚动轴；review tab panel 不再限制为 200px
  内层滚动，底部操作区不再 sticky 覆盖审核内容。布局回归会直接锁定这两个 CSS 不变量。

## 2026-08-21 Plugin Writer final delivery batch

- Whole-branch review fix wave replaced cosmetic adjustments with compiler-code-authored typed issues and
  closed workflow adjustments. Public HTTP E2E proves archive SHA/output warning change, generation
  invalidation, exact warnings and no SQLite injection. Generation is server-authoritative; build and
  regeneration are observable asynchronous stages; terminal retry creates a fresh generation.
- Review now carries explicit crop/transparency, hierarchy/geometry/text, naming-conflict, closure and
  integrity facts. Unkeyed selection previews are not position-joined, and preview fetch failure blocks
  approval. Final regression is `1146 passed, 4 skipped`; plugin `171 passed`; Web Console `30 passed`;
  Ruff, strict mypy, both typechecks/builds and package `5 passed` are green.

- 公开交付 E2E 现覆盖中立 PNG manifest/resource 上传、批准前下载阻断、四类 review、
  服务端声明 adjustment、v1 永久失效、v2 精确 warning acknowledgment、whole-candidate
  approval、两次下载的 bytes/name/size/SHA-256 一致及生产 archive validator 闭包。请求
  记录证明未使用 template、pairing、`/v1/agents/`、existing-project 或 Project Binding 路由/字段。
- no-special-case 扫描已扩展到 `src/figma_to_fgui`、`apps/figma-plugin/src`、
  `apps/web-console/src/figma` 与 `rules/default`，并以 backend 和 Writer panel 注入测试证明
  village/rank/node 唯一标记会被检出；通用需求方 mapping 仍只有原 allowlist。
- 包装门会验证 Writer 可见文案、新路由 token、`defaultMode="writer"`，并对交付 ZIP 四个
  成员与经验证构建输入做逐文件 SHA-256 字节闭包。
- 最终自检：focused `5 passed`；Python 全量 `1141 passed, 4 skipped, 1 failed`，唯一失败是
  已知且未修改的 Task 3 报告 `Capture/capture` 大小写断言（测试和报告均来自
  `0bb55f93`）；Ruff 通过；strict mypy 58 个源文件通过；Figma plugin `171 passed`、
  typecheck/build/package `5 passed`；Web Console `29 passed`、typecheck/build 通过。
- 2026-08-21 新鲜 GUI 尝试未通过：Figma 和 FairyGUI Editor 6.1.4 都能启动且返回唯一
  窗口，但 native capture 均失败于 `0x80004002`；Figma 元素输入还失败于
  `coordinate input geometry is unavailable`，FairyGUI 可访问性仅暴露标题栏且 `Ctrl+O`
  未产生可目标的打开对话框。因无法证明 Figma 当前选择为中立数据，未上传、未截图、未下载；
  也未宣称新鲜 Editor open/save/reopen 通过。详情在
  `docs/validation/2026-08-21-plugin-writer-final-acceptance.md`。

## 2026-08-20 Plugin Writer frontend batch

- Figma 插件入口显式启用 `defaultMode="writer"`；`ProjectWorkflowPage` 默认仍为 `legacy`，因此 Web Console
  四步 create/update 流不变。Writer 的“更新现有工程”只从插件 overflow 进入，并复用隔离的旧 update 流。
- Writer 客户端现覆盖 selection upload、新建候选 start/poll、严格 review、服务端声明 adjustment、regenerate、
  whole-candidate approve/reject、鉴权 preview，以及 approval-gated ZIP 下载；下载同时复验安全文件名、精确大小
  和 SHA-256。公开错误新增 `review_required` / `stale_candidate`。
- 360×680 Writer UI 使用单工程名、只读 FairyGUI 6.1.4/新建独立工程、inline stages、四类 review tab、
  真实 evidence 标签、candidate generation 失效、warning 重新确认、统一批准下载与再次下载；无模板、
  Project Binding、既有资源字段、per-file approval 或业务样例特例。
- 后端最小前端集成补丁为 review check 投影闭合 `allowed_strategies`，并从 adjustment request 删除冗余且不应公开的
  selection fingerprint；候选 owner/ID/generation/issue/node 及不可变 selection 关联仍由服务端验证。

## 仓库与分支

- 工作树：`C:\Users\<user>\Documents\figma转fgui\source\.worktrees\writer-review-alignment`
- 当前分支：`codex/writer-review-alignment`
- 公网 GitHub 远端：`github = https://github.com/oarngeftansy/fgui.git`；交付必须推送到该远端的同名分支。
- `origin` 是本机恢复仓库 `C:\Users\<user>\Documents\figma转fgui\_recovery_repo`，不是公网 GitHub 远端。
- 2026-08-24 交接前基线为 `d78052f`（`fix: support loopback plugin development`）；最新代码与记忆由本次 handoff 提交承载，具体 hash 以分支 HEAD 为准。

## 已完成的通用管线

- Figma 插件选择数据和 REST 数据可进入规范化层。
- 已建立严格、可序列化且可验证的 UIR v1。
- 已建立 FairyGUI 通用能力分析与 FGUI Plan 编译/验证。
- 已覆盖基础容器、文本、图片/矢量资源、组件引用、有限的矩形/圆角/图片遮罩、九宫格事实和安全 PNG 降级。
- 资源具有逻辑身份、内容哈希、导出参数哈希、MIME/格式、尺寸、消费者和降级理由。
- UIR/Plan 不允许混入目标 FairyGUI 工程真实 ID、私密字段、本地路径或原始秘密数据。
- 复杂能力不能被可靠表达时会阻断，而不是生成表面成功但失真的结果。
- 已重建插件实际加载的 `apps/figma-plugin/dist/code.js`，并验证与 fresh build 字节一致。
- 组件映射需求模板位于 `docs/templates/`，其中 JSON 是候选格式，不是已经验证的生产映射。

## 最近一次完整自检证据

- Python：`749 passed, 2 skipped, 0 failed`。
- Python focused：`355 passed`；golden：`5 passed`。
- Ruff：通过。
- mypy：48 个源码文件，0 error。
- Figma 插件 Vitest：`162 passed`。
- TypeScript：通过。
- 插件可复现构建/打包检查：`5 passed`。
- 已安装 CLI 的 `build-uir → build-fgui-plan → parse/validate` 冒烟验证成功，`bindable=True`。
- 详细报告：`.superpowers/sdd/final-fix-report.md`。

## 2026-08-18 FairyGUI 6.1.4 Writer 方言证据

- `tests/fixtures/fgui-editor-6.1.4/minimal/` 是从已由 FairyGUI Editor 6.1.4
  打开并保存的 `FairyGUI-project3` 提炼的项目中立最小格式证据；不包含村庄或其他业务资源。
- 新建工程方言常量为 `FAIRYGUI_VERSION="6.1.4"`、`PROJECT_SUFFIX=".fairy"`、
  `ASSETS_DIRECTORY="assets"`；`parse_editor_fixture` 通过唯一工程标记、唯一包清单及其组件 XML
  识别编辑器夹具。
- Task 1 评审修复后，方言 fixture parser 会严格验证 marker/package/component 根与结构、
  observed marker `version="5.0"`、Unity target、ID 唯一性，以及资源 package-virtual POSIX
  路径、package-root 边界和 symlink/reparse point。资源路径允许单个 leading `/`；组件 XML
  `name` 可选且不定义组件身份；`publish` 只验证唯一的空元素结构，不猜测属性语义。
- Task 1 修复自检：`794 passed, 3 skipped`，Ruff 和 mypy 通过。
- 2026-08-19，中立化后的最终 `Minimal` fixture 已通过 FairyGUI Editor 6.1.4 两次保存、
  中间关闭重开的 GUI gate；无 repair/migration modal，三个 tracked fixture 文件保持字节一致。
  Editor `.objs/` 缓存已删除并由 fixture-local `.gitignore` 排除。

## Writer 实现状态与保留边界

- 最终安全收口后，Pillow probe 使用绝对 Python/绝对脚本、`-I`、可信 cwd 和
  最小 Windows 环境 allow-list，不继承 PYTHONPATH 或秘密；恶意 CWD `PIL` shadow 不会执行。
  资源编码字节限制为单文件 64 MiB、构建合计 256 MiB，目录加载和内存 payload gate
  均在 probe 前 fail closed。Editor 门禁的机器可读证据位于
  `docs/validation/2026-08-18-fgui-6.1.4-new-project-editor-transcript.json`。最终全套为
  `1072 passed, 4 skipped`，Ruff 通过，strict mypy 56 个源码文件通过。
  合计 payload cap 在每个文件读取时传入剩余预算，并在保留实际 bytes 前复验；
  精确边界允许，stat 后增长超出剩余预算时 fail closed。机器可读 Editor transcript
  由 golden 测试重建确定性 ZIP 后对精确成员闭包逐项重算 SHA-256。

- Writer Task 10 自动化验收已收口：通用工程 ZIP 以固定 SHA-256
  `bf62cd2789a7d0a44336e28a4c257d4fe91bee34c7673738bdf3f0662217c4ca` 作字节级
  golden，安全回归覆盖路径逸出/绝对路径、重复与 casefold 成员、symlink ZIP
  成员、DOCTYPE 和非法图片；失败不发布。村庄样例仅通过通用映射输入进入
  UIR/Plan，并因 UIR v1 无可生成组件树以 `fgui.component.definition_missing`
  在 Writer 之前 fail closed；无页面特例，无 Project Binding。完整自检为
  `1061 passed, 4 skipped`，Ruff 通过，strict mypy 55 个源码文件通过。单一中立代表
  `GenericWriterFixture` 已在 FairyGUI Editor 6.1.4 完成打开、保存、关闭、重开、
  再保存与关闭；未观察到 modal，4/4 声明文件保持字节哈希一致。

- Writer Task 9 已实现新建工程 CLI：`fgui-tool build-fgui-project PLAN CONFIG ASSET_DIRECTORY
  OUTPUT_DIRECTORY`。CLI 仅接受严格、可绑定的 Plan v2 和 `NewProjectConfig`，通过闭合
  asset manifest 加载资源，拒绝未声明文件、绝对/穿越路径、Windows casefold 冲突、
  symlink/reparse 和非普通文件；成功结果不暴露本地路径，失败不发布 ZIP。中立端到端
  fixture 已验证 ZIP 可重开，未引入 Project Binding、需求方映射硬编码或村庄特例。
  审查收口后，Plan/config/asset manifest 会拒绝重复 JSON key；Plan/config 还要求 exact builtin header
  与 canonical bytes。资源使用 handle/path identity 和前后元数据复验、link/reparse component 检查及闭包
  快照复验，检测到 swap 即 fail closed。中立 E2E 现含真实 1x1 PNG consumer，连续两次 CLI 构建 ZIP
  字节一致。最终审查又加入 POSIX `st_ctime_ns` 与跨平台最终内容摘要复读；Windows 不把创建时间误作变更时间。
  Task 9 自检：focused `39 passed`，全套 `1052 passed, 4 skipped`，Ruff 通过，mypy 55 个源码
  文件 0 error。

- Writer Task 8 已实现新建工程五道门与原子 ZIP 发布：输入 payload、Manifest、XML/file closure、落盘目录
  重开和 ZIP 重开全部通过后，才以 `os.replace` 发布。目录/归档门拒绝 link/reparse、未声明文件、路径
  traversal、重复或 Windows casefold 冲突成员、Unix symlink/special mode、CRC/资源哈希/XML 引用错误；
  ZIP 元数据与排序固定，重复构建字节一致。评审修复后所有 public build error 均切断 cause/context 与
  traceback 私密异常链，归档 SHA-256/大小及返回字段在 publish 前确定，临时目录也在最终 `os.replace`
  前清理；cleanup 失败同样走闭合错误并尝试 fd close/tmp unlink，双重清理均被系统拒绝时只保证隐藏 tmp
  从未 publish，不承诺无残留。已有 BuildError 也只提取 allow-list boundary code 并重建静态错误，不信任
  其 diagnostics/cause/context。publish 后不再 read/hash/stat。最终自检为 `1037 passed, 4 skipped`，
  Ruff 与 55 个源码文件
  strict mypy 通过。

- Writer Task 7 已实现 FairyGUI 6.1.4 的纯内存 XML serializer 与独立 XML/file-closure gate：
  只接受二次 canonical 验证的 `NewProjectManifest` 和匹配的 `ValidatedAssetPayload` 闭包，使用
  lxml 构树并固定 UTF-8/LF、属性/资源/组件顺序、finite canonical decimals；闭合分派覆盖 7 个
  Plan node type，以及组件根上的 rectangle clip、rounded clip、image mask 三条实证路径。
  `package.xml` 闭包覆盖 components/images、same-package component `src/pkg`、loader `ui://`、
  nine-slice 与资源文件；独立 gate 会安全重解析并拒绝 DOCTYPE/entity、未知结构、坏引用、非 canonical
  数字、全局 ID 冲突、Windows casefold 路径冲突和任何未声明/缺失文件。parent-local 几何在 group
  扁平 XML 中累加为 component-local 坐标。golden matrix 覆盖 11 个中立样例。
- Task 7 本机最终回归：focused `64 passed, 1 skipped`；全套 `995 passed, 3 skipped`；
  Ruff 与 54 个源码文件 strict mypy 通过。
- Task 7 原定 11 个对象 fixture 的逐一 GUI round-trip 已根据用户决定缩减：
  11 个中立样例仅声明 serializer/XML golden 自动化覆盖，不声明 11/11 真实 Editor
  通过。真实 Editor 证据限定为 Task 10 的单一 `GenericWriterFixture` 双保存闭环。
  Unity 窗口 state/screenshot API 不受支持，不声明截图或完整 accessibility 审计。
  嵌套 container native mask 因无实证编码继续
  fail closed；三个 native mask 路径当前只在组件根目标上发射。
- Writer Task 6 已实现纯内存 `NewProjectManifest` 编译与验证：输入会重新验证
  Plan bindability/语义和 validated payload 闭包；所有目标 ID 只在 Manifest 层按逻辑键分配并
  复验。定义按依赖拓扑序位于 roots 之前，document tree 与 definition-local tree 使用
  独立 ownership domain；对象保留父级局部几何、children/z-index、visibility、mask scope/order、
  resource consumer 和 raster-consumed UIR refs。Manifest gate 迭代校验路径/ID/所有权/引用/消费者/
  mask/raster/环/深度，公开诊断固定排序且含建议动作；不写磁盘或 XML。
  最终收口后，Manifest standalone gate 会在任何 JSON coercion 前检查 Plan/Config/
  Manifest 的 exact builtin headers，并扫描整个 Manifest 字符串闭包；仅用户可见
  `TextPlan.content`/run content 豁免。`projectName` 同时经过 target-name policy。

- Writer Task 4 已实现资源 payload 输入门：它要求 Plan/resource payload 精确键集合、流式 SHA-256、
  Pillow 实测的 PNG/JPEG/WebP 格式/MIME/尺寸和九宫格边界完全一致；截断图和解压炸弹会拒绝。
  该阶段原本拒绝 SVG，已由 2026-08-25 的安全自包含 SVG 输入门替代。Pillow 探测在受控子进程完成，
  父进程不修改 Pillow 全局状态；失败仅输出排序后的公开诊断，且不会包含资源字节。

- 通用 FGUI XML Writer 、新建工程 CLI、五道校验门和原子 ZIP 发布已实现。
- FGUI Plan 已升级为 schema v2，组件引用必须通过 `definitionRef` 闭包到 Plan 内的完整
  `componentDefinitions`；无组件引用的严格 v1 可显式迁移，包含组件引用的 v1 必须重新编译。
- Plan v2 定义局部树与定义引用图的 256 层边界均从实际 root 用迭代 DAG 最长路径计算，
  不依赖逻辑 ID 排序；reviewed component→raster fallback 会按最终 decision 安全消费后代，
  并在最终所有权确定后抑制已被外层 fallback 消费的内层 raster MaskPlan/专属资源 consumer；
  mask 最终还必须闭包到实际 emitted node target，blocked/definition-missing/不可发射祖先下不保留
  orphan mask/resource；不可栅格化行为后代继续阻断。评审修复后全套为 `819 passed, 3 skipped`。
- 当前 UIR v1 的组件定义只有来源/名称/属性元数据，没有独立可生成节点树；因此编译器不会把
  verified candidate 猜成组件定义。缺少完整定义时只接受上游明确批准的 PNG raster fallback，
  否则以 `fgui.component.definition_missing` 阻断。
- 真实 FairyGUI 6.1.4 新建工程加载验收已对单一中立代表工程完成；
  该证据不扩展为所有对象类型的真实 GUI 覆盖。
- Controller、Gear、List、复杂 Auto Layout、不可表示的 transform/rotation 和仍无法映射的 RichText 属性目前按规则阻断或进入可编辑风险审核；可表达的多 run 文本继续保留为原生文本。
- REST 路径对遮罩比实时插件路径更保守；歧义遮罩会阻断。
- “更新已有工程”的 Project Binding、existingResource 复用/所有权语义属于后续可选能力，不应阻塞新建工程主流程。

## 已完成阶段：通用 XML Writer

已实现仅消费已验证且 `bindable=True` 的 FGUI Plan 的新建工程链路：
`NewProjectConfig` / payload 验证、Manifest 编译、FairyGUI 6.1.4 XML、五道重开校验、
确定性 ZIP 和 CLI。该链路不依赖已有工程扫描，已通过自动化矩阵和单一中立代表
Editor 双保存闭环。

### 2026-08-18 已批准的 Writer 设计决策

- 新建工程正式产物是可由 FairyGUI Editor 6.1.4 直接打开的完整工程 ZIP。
- 第一版每次生成一个工程、一个包；每个 Plan root 是一个顶层组件，共享资源和自包含组件定义位于同包。
- Writer 输入为 `Validated FGUI Plan + NewProjectConfig + AssetPayloadSet`。
- 采用 `NewProjectManifest` 两阶段架构；它只为新工程确定性分配 ID 和路径，不是 Project Binding。
- `componentReference` 必须引用 FGUI Plan schema v2 中完整可生成的自包含组件定义；Writer 不从 candidate 名称猜组件。无组件引用的 v1 可显式迁移，其他 v1 必须重新编译或安全降级。
- 需求方 7 项通用组件映射仍是 candidate 需求数据，不包含组件定义；新建模式下缺少定义时只能按安全 raster fallback 或 unsupported 处理。
- 设计规格：`docs/superpowers/specs/2026-08-18-new-project-fgui-xml-writer-design.md`。
- 已完成实施计划：`docs/superpowers/plans/2026-08-18-new-project-fgui-xml-writer.md`，
  10 个 TDD 任务均已收口。

## 当前交接状态

- 2026-08-25 子 Frame 不再因 `clipsContent=true` 被 Writer 提升为额外的 `Clip_*` 包内组件。所有嵌套 native clip 在 Writer 边界仅移除 FairyGUI 6.1.4 普通显示列表无法原生表达的嵌套裁切角色；原 clip graph 恢复成普通 container，其矩形外观进入 `backgroundGraph`，从而满足 FairyGUI group 必须位于成员之后的 XML 合同。Frame 本体、原名、位置、尺寸、可见性、内部可编辑对象和父子顺序继续留在同一个顶层组件显示树；根组件可表达的 `overflow="hidden"` 不变，显式复杂 mask 仍按既有安全规则处理。该规则只按嵌套层级和能力事实生效，不含页面名、节点 ID 或业务样例特例。

- 2026-08-25（已被上条规则取代）曾尝试把嵌套 `clipsContent=true` Frame 提升为内部 `Clip_*` 组件以承载 `overflow="hidden"`；真实 Editor 验收确认这会改变普通 Frame 的资源拓扑，因此不再采用。插件侧“不整块 PNG”仍保留，显式复杂 mask/effect 仍按原规则保守处理。

- 2026-08-25 ELLIPSE PNG 首次真实 Editor 复验发现资源裁剪框与节点编辑框混用：示例 PNG `141×141`，manifest 却按旋转后的 `378×378` absoluteBoundingBox 放置，导致扇形被放大和漂移。插件现对所有实际导出资源统一优先使用 `absoluteRenderBounds` 作为位置/大小，缺失或非法时才回退节点 bounds；未导出的原生节点不变，PNG 仍保持 rotation=0。该规则按资源事实生效，不含页面名、节点 ID 或业务样例特例。

- 2026-08-25 最新真实 selection `cf4e...` 的 93 个节点证明：任意路径 6 项虽已为 PNG，仍有 15 个 `ELLIPSE` 被当作原生 Graph；而 Figma ELLIPSE 可实际表示圆弧、圆环和部分 sweep，FairyGUI 普通 ellipse 无法还原其角度、inner radius 与路径轮廓。插件现把 ELLIPSE 纳入统一 vector-family PNG 边界，使用 Figma 的轴对齐透明 PNG 和烘焙 transform，rotation 归零；普通 RECTANGLE、文本、图片和容器仍按原能力保留可编辑。玩家经验仅作为混合 PNG/Graph/文本层级的通用回归输入，canonical sibling order 不反转。

- 2026-08-25 首次真实新策略上传（selection `2187e21e…`）暴露后端分类遗漏：4 个 `BOOLEAN_OPERATION` 已上传 `vector_asset + PNG`，但 `base_decision_for_node` 仍先按 native transform 阻断，造成 `fgui.unsupported.transform` 和 4 个 unused asset。现所有“单一已验证资源支持的叶节点”会吸收已烘焙的 transform/visual-style 差异，同时交互行为仍继续阻断；这也覆盖同性质的普通图片叶节点，不按页面/名称/ID 特判。同一真实 selection 修复后重放为 93 nodes、35/35 resources、0 blocking、ZIP 1,776,904 bytes。专项 `110 passed`，全量 `1230 passed, 4 skipped`，Ruff/mypy/diff check 通过；服务需在该提交后重启。

- 2026-08-25 arbitrary vector 资源策略已从 SVG 改为 Figma 透明 PNG：`VECTOR`、`BOOLEAN_OPERATION`、`STAR`、`LINE`、`POLYGON` 保留 `vector_asset` 语义但声明/导出 `image/png`；纯色矩形、椭圆、可表达描边/圆角仍为原生可编辑 graph。vector PNG 使用 Figma axis-aligned bounds 且 selection rotation 固定为 0，后端拒绝 `vector_asset + SVG` 和非零 rotation，防止 FairyGUI 二次旋转。嵌套 group 回归证明 group 保留显式 `xy/size`，成员保持 component-space `xy/size`，不由 PNG 固有尺寸缩放。插件全量 `221 passed`、typecheck/build/dist parity 通过；Writer 专项 `194 passed`；Python 全量使用系统短 ASCII basetemp 为 `1229 passed, 4 skipped`；Ruff/mypy 通过。提交为 `5b7c788` 与 `a63588b`，真实 Editor 视觉仍待用户用重启后新生成包复验。

- 2026-08-25 上一次基于 Editor 左侧列表方向做的全局 sibling 反转经用户真实 GUI 复验证明错误：背景等大量对象被移到最上层。现已完全撤销反转，Writer 重新按 canonical `childObjectRefs` 顺序写入，group/mask 的原有 XML 门保持。真实 93-node selection 重放 ZIP 开头恢复为 `Bg → n37 → n38 → ...`，背景在内容之前。专项 `125 passed, 1 skipped`，全量 `1226 passed, 4 skipped`，Ruff/mypy/diff check 通过。先前“Figma/FGUI 必须全局反转”的结论已被此真实 GUI 证据推翻，不得继续使用。

- 2026-08-25 按钮文字的未居中视觉已定位为像素行高丢失，而非 align/font 丢失。真实 selection 中 `None`、`Village Elder`、`BUTTONNAME`等均为 `fontSize=52`、`lineHeight=40`、center/middle；旧 XML 无 lineHeight，现 UIR/Plan 保留 typed `lineHeight`，Writer 输出 `leading=-12`。真实重放 ZIP 已核对以上文字均同时含 `fontSize="52" leading="-12" align="center" vAlign="middle"`。像素行高不再进入 review risk，百分比/非法/缺 fontSize 仍保守拒绝；旧 generate 路径同步支持。专项 `260 passed, 1 skipped`，全量 `1226 passed, 4 skipped`，Ruff/mypy/diff check 通过；Editor 视觉待用户以新包复验。

- 2026-08-25 真实 Editor 对比发现同组圆形/圆环/文本在 FGUI 的列表和视觉堆叠整体反向。根因是 Figma children 为前景→背景，FGUI XML 为背景→前景。现 Writer 在每级同级输出时反转，同时保持 mask source 先写、plain group 后写。真实 selection 重放中关键组 XML 已呈现 `对 → 建筑 1 → 5_5 → Build Level → 进度 → Ellipse 332 → Ellipse 328`的背景→前景顺序，Editor 列表反向展示后与 Figma 顶→底一致。专项 `124 passed, 1 skipped`，全量 `1225 passed, 4 skipped`，Ruff/mypy/diff check 通过；真实 GUI 视觉待用户用新包复验。

- 2026-08-25 真实 Editor 对比发现文本颜色偏蓝紫：Writer 把 Figma/Plan `#RRGGBBAA` 文本颜色原样交给按 `#AARRGGBB` 读取的 FairyGUI 6.1.4。现仅在文本 XML 边界转换基础色、描边色和 RichText run color，6 位色不变，GraphPlan 已是 ARGB 的 fill/line color 不做二次换序。真实 selection 重放后示例 XML 为绿 `#ff33900c`、棕 `#ff5d371c`、浅色 `#fffff3e3`；专项 `92 passed, 1 skipped`，全量 `1224 passed, 4 skipped`，Ruff/mypy/diff check 通过。真实 Editor 视觉仍由用户用重启后新包复验。

- 2026-08-25 用户在 14:43 实际打开重启后新包仍见 `Int32.Parse`，证明先前只收口 rotation 不完整。对当次 `9-FairyGUI.zip` 查验发现 rotation 已为整数，但 display object `xy`/`size` 仍含 Figma 小数。现 Writer 已将组件 size 及对象 xy/size/rotation 全部按 FairyGUI 6.1.4 Int32 合同输出，内部 Plan 仍保留浮点事实。最新 93-node selection 重放成功，组件根外 92 个 display objects 的非法 xy/size/rotation 数为 0，剩余小数属性为 0；dialect `87 passed, 1 skipped`，全量 `1222 passed, 4 skipped`，Ruff/mypy/diff check 通过。此结果仍等待用户的真实 Editor 打开复验，不声明 GUI 已通过。

- 2026-08-25 真实 Editor 打开包时的 `System.Int32.Parse` / `FObject.Read_beforeAdd` 已定位为 Writer 将 Figma 浮点 rotation（如 `40.00000005531198`）直接写入 XML。现已按 FairyGUI 6.1.4 Int32 合同序列化最近整数角度，XML 门会拒绝小数/越界 rotation。真实最新 selection 重放为 1 root、93 nodes、35/35 resources，Writer 成功生成 1,774,378-byte 诊断 ZIP；focused `155 passed, 2 skipped`，全量 `1222 passed, 4 skipped`，Ruff/mypy/diff check 通过。

通用新建工程 Writer 的本轮原生可编辑、审核证据和审核话术修复已经实现并通过沙箱全量门。
下一阶段不是新增功能，而是按本页 2026-08-24 验收顺序进行真实 Figma → ZIP → FairyGUI Editor
通用验收；发现问题时继续修通用规则并补正反例。Project Binding 仍只是“更新已有工程/复用
既有资源”的后续可选模式，不应在未获得新授权时自行开始。

## 2026-08-20 Plugin Writer Task 1：committed-selection workflow

- 新增纯 `build_selection_new_project` 编排器：仅使用 selection、闭合资源目录、公开 fingerprint、
  project name 和输出目录，按 `selection → normalize → mapping → UIR → Plan v2 → Writer ZIP` 执行；
  固定 `fgui-6.1.4-v1`、rule 1、`Generated` 包、FairyGUI 6.1.4 与 Unity。
- 工作流会在 Writer 之前复验 selection 资源的 logical/source asset identity、MIME、SHA-256 和字节大小；
  Writer/转换异常只会变成新的 allow-listed public diagnostics，并切断 cause/context。
- 组件 candidate 不升级为可生成定义；selection 没有 definition tree 时，命中的 INSTANCE 以
  `fgui.component.definition_missing` 阻断。focused workflow tests 为 `10 passed`，Ruff 与 strict mypy
  （57 source files）通过。

### 2026-08-20 Task 1 review fix

- candidate blocking 现在直接使用 committed `SelectionManifest` 中的 canonical Figma INSTANCE ID；
  不再把 catalog `nodeIds` 与 normalize 后的派生 ID 比较，也不依赖显示名猜测。未验证 candidates
  不传入 UIR compiler，因此不会被升级为可生成 component。
- selection assets 与 Plan logical asset IDs 必须精确集合闭合；payload reader 采用单项/合计上限、
  regular-file handle、读前/读后身份复验和有界读取。UIR source asset ID 复用
  `uir_compile.uir_asset_id` 的唯一 canonical 实现。

### 2026-08-20 Task 1 second re-review fix

- committed-selection workflow 在 normalize 前以 catalog 原始 source `nodeIds` 或 catalog
  `names` 的精确 OR 语义匹配所有 INSTANCE；未匹配或 candidate-only 的实例稳定以
  `fgui.component.definition_missing` 关闭，歧义或 `conflict` 映射稳定以
  `fgui.writer.workflow.mapping_conflict` 关闭，均不会发布 ZIP。
- workflow 依据 source/normalized 同构树桥接 original Figma ID 到 normalized UIR ID，并只把
  non-candidate catalog copy 传给 UIR compiler；因此 verified/missing/conflict 状态保持标准
  UIR→Plan 行为。`missing` 仅在已有有效 raster asset 时走显式 fallback，而不匹配实例不能沉默
  降级为 native image。

## 2026-08-20 Writer acceptance Task 1

- 已新增隐私安全的六用例 acceptance runner：TC-01/TC-02/AC-01/TC-03/TC-04/TC-05。
  它经现有 CLI loader、Writer build、archive validator 和通用 mapping→UIR→Plan 路径执行，
  canonical JSON 使用 UTF-8、排序 compact key 与唯一 LF，不记录本地绝对路径、环境值、资源原始字节或异常链。
- AC-01 仅消费 durable Editor transcript 并明确 `guiActionPending=true`；PNG evidence cards、fresh GUI
  action 和最终 acceptance pack 仍由后续 Tasks 2/3 完成。Task 1 focused verification：`15 passed`，
  runner contract：`3 passed`，Ruff 与 `git diff --check` clean。
- 评审修复：TC-05 现将 non-bindable village Plan 真实传入 public `build-fgui-project` CLI，并把实际
  `tc-05-output` 作为 publish argument；它同时记录 definition-missing 与闭合 `PLAN` rejection，再验证零 ZIP。
  village fixture marker scan 已抽取到 `tests/support/village_writer_regression.py`，由 acceptance 与集成回归共享；
  复验 acceptance + village regression 为 `8 passed`，Ruff/mypy clean。
- 直接调用修复：runner 在 import shared test-support helper 前，从自身 resolved file 建立可信 repo/src import
  roots，不依赖 ambient CWD/PYTHONPATH。sanitized-PYTHONPATH subprocess 覆盖 exact `python scripts/run_new_project_writer_acceptance.py --help`
  与最小完整 CLI 运行；acceptance + village regression 最新为 `9 passed`，Ruff/mypy clean。

## 2026-08-20 Writer acceptance Task 2

- 验收 runner 现在能把固定六用例结果渲染成六张本地、自包含的 `1440x1000` HTML evidence cards；所有动态
  文本均经 HTML escaping，卡片不加载网络字体、脚本、图像或其他外部资源，且拒绝私有绝对路径。
- `finalize_screenshot_closure` 在最终模式下要求每个 canonical PNG 引用都存在、具有 PNG/IHDR、精确
  `1440x1000` 尺寸，且将 SHA-256 写回结果；已有 hash 不匹配或任一缺图都会 fail closed。仅明确
  `screenshots_pending=True` 或 CLI `--screenshots-pending` 能在 Task 3 前跳过该闭合，且不会记录 hash。
- Task 2 focused verification：`11 passed`；Ruff、strict mypy（56 source files）与 `git diff --check` 通过。
- 本任务无新的通用过程经验；Task 3 仍负责实际 Playwright/FairyGUI 执行、PNG 生成与最终人类报告。
- Task 2 privacy review follow-up：public-text validation is now shared by card rendering and canonical
  JSON serialization. It rejects embedded POSIX absolute, Windows drive-rooted, rooted-backslash, and
  UNC paths before any card/result bytes are written. The focused suite is now `16 passed`.
- Task 2 privacy re-review: the public-path check now uses a token-aware scanner rather than a fragile
  slash regex. It rejects POSIX root/dot-segment/repeated-separator and file-URI forms, while preserving
  safe public codes such as `image/png` and constrained `ui://...` references. Focused verification is
  now `22 passed`.
- Task 2 HTTP re-review: `http://` and `https://` are not public-text exemptions. This prevents an
  external-URL token from hiding a local-looking suffix, keeps cards self-contained, and leaves only a
  constrained `ui://...` URI exemption. Focused verification is now `26 passed`.

## 2026-08-20 Writer acceptance Task 3 handoff

- AC-01 now consumes the tracked fresh, bounded `GenericWriterFixture` Editor 6.1.4 transcript rather
  than a pending placeholder. It records exactly two save rounds, returned title
  `GenericWriterFixture`, `delayedCloseObservation=true`, final window count zero, no observed modal,
  the unsupported state-screenshot API error `0x80004002`, and 4/4 equal pre/post hashes. The runner
  rejects transcript additions or alterations instead of widening GUI claims.
- The runner can generate the final canonical JSON and six-section Markdown report only after all six
  PNGs close at exact `1440x1000`; each report section repeats the machine fields and has exactly one
  local screenshot link and matching SHA-256. `--screenshots-pending` can render cards for capture but
  cannot write the final report.
- Evidence cards carry the `1440x1000-no-scroll` layout contract. A headless Edge measurement of all
  six freshly rendered cards found `documentElement.scrollHeight == 1000`; the compact layout keeps the
  decisive evidence visible without full-page overflow.
- The existing user-supplied TC-01 and TC-05 PNGs were inspected but not changed; their headers are
  `1440x1080` and `1440x1042`, respectively, so strict finalization will intentionally require their
  recapture alongside AC-01 at exact `1440x1000`.

## 2026-08-20 Plugin Writer backend batch

- 插件现可从 committed selection 创建专用新建 FairyGUI 工程候选；公开合同严格、
  owner-only，产物路径不公开。候选使用随机 32-hex build ID、selection fingerprint/
  request identity、generation 和 lease 独立持久化。
- 完整后端状态机已包含 typed review，严格 warning acknowledgment，whole-candidate approve/
  reject，闭合 adjustment strategy，以及从原不可变 selection 全管线重生的新 build ID/
  generation；旧候选对 review/preview/approval/download 永久失效。
- 候选 ZIP 在 review、approval、preview、download 和 restart 都会重做 expected storage root、
  regular-file/link/reparse identity、size 和 SHA-256 闭合；失败或篡改不可下载。
- 本批次 focused 为 `19 passed`，旧 API/store 相关回归 `107 passed`，Ruff 通过，
  strict mypy 对 58 个 source files 通过。全套排除一个未修改的 acceptance 报告文案
  大小写断言后为 `1140 passed, 4 skipped, 1 deselected`。

## 2026-08-21 Plugin Writer portrait review workflow

- Figma 插件 Writer 审核窗口改为 `640×800` 竖屏，并把候选展示拆成三个独立步骤：先展示自动转换，
  再逐项展示带图示的建议/阻断审核，最后展示确认下载与折叠的工程详情；不再常驻四类 tab 和转换目录。
- 自动转换项明确区分原生转换与视觉保真图片；建议/阻断项逐个显示 Figma 来源证据与 FairyGUI 结果。
  缺少真实截图时 UI 必须标注“结构示意（非截图）”，不得冒充真实画面。
- `复制到 Figma 审核区` 使用新的 strict/bounded plugin message，把已认证生成 PNG 与来源节点副本放入
  当前选择右侧的独立顶层 `FairyGUI 待审核` frame。审核 frame 使用 plugin-data ownership 标记，
  不会仅凭同名删除用户 frame，也不会重排或修改原来源节点。
- 当前验证：Web Console `38 passed`、Figma plugin `185 passed`、Writer focused `16 passed`、
  plugin contracts/bridge `28 passed`，两端 TypeScript 与 Web build 通过，tracked plugin dist/package parity `5/5`。
- 功能基线 `782520a` 已在清理前推送到 GitHub `oarngeftansy/fgui`的
  `codex/writer-review-alignment` 分支；本机 `origin` 仍仅作 recovery remote。
- `ai-codebase-cleanup` 首批只清理 Writer 三段式流程已替代的四标签审核 CSS 和重复 shell
  声明，并把依赖旧 `.writer-tab-panel` 的测试改为验证当前单滚动区/独立操作栏。Web Console
  后续整理又把预览下载、Blob/Object URL 所有权和释放提取为专用 Hook，删除未读取的
  `runResult` 重复状态和未使用测试导入。Web Console 全套 `40 passed`，TypeScript 常规/未使用符号检查
  与 Vite build 通过。候选生成/调整/失效仍保持在同一状态机内，避免为拆文件破坏取消令牌与服务端失效顺序。

## 2026-08-26 Writer 新项目实例内联与空自动页修复

- “105 个图层、39 个资源，但没有可自动确认项”不是正常分类结果，而是候选在产物生成前被
  10 个 `component_definition_missing` 阻断后，前端仍落到自动转换空态。排查此类空态必须先核对
  candidate 的 artifact/review 状态，不能从空卡片推断源图层不存在。
- New Project 不启用 Project Binding：可读 INSTANCE 已提交完整子树时，即使存在 verified mapping，
  Writer 也必须内联其原有子层级，不得升级为缺少外部定义的 `componentReference`。
- 不可见且没有可读子树的叶节点不参与视觉输出；其不可表达变换/视觉效果不应阻断归档。Writer 保留
  对应隐藏容器及 `visible=false`，同时不要求外部组件定义。
- 使用当前真实 committed selection 回放：105 个节点、39 个资源全部进入有效 ZIP；90 项原生自动转换、
  15 项最小图片保真、0 阻断、0 component reference，ZIP 为 761685 bytes。
- 完整沙箱验证：`1246 passed, 4 skipped`；Ruff、strict mypy（61 source files）和 `git diff --check` 全通过。

## 2026-08-26 Writer 不得用几何越界代替层级语义

- 子节点 bounds 越过 Frame 边界，不足以证明应将可见交集裁成 PNG；部分不显示可能由后续同级图层遮挡造成。
  selection 必须保留完整子树、原 bounds 和 z-order，只根据 Figma 明示的 mask/clip 能力字段建模，不得从交集几何
  反推并裁剪叶节。含 TEXT 的卡片越界时，文字仍必须是可编辑 text。
- hidden 是两条独立责任：资源导出时在临时隔离副本上设 `visible=true` 以取得完整 PNG，但不修改源 Figma；
  产物对象仍使用 FairyGUI 6.1.4 已验证的 `visible="false"`。不能用空 container 替代隐藏图像/图形。

## 2026-08-26 PNG 保真不得继承 Figma 的裁后 render bounds

- `absoluteRenderBounds` 可能已经受祖先裁切或同级遮挡影响，它不是资源节点的完整布局边界。将矢量、图片、
  阴影或最小 mask 局部转成 PNG 时，只允许改变表达方式，不得把该字段当成 FairyGUI 的位置和尺寸，否则会把
  Figma 当时的局部可见范围永久烘焙成裁图。
- selection manifest 对所有节点统一保留 `absoluteBoundingBox`；PNG 仍清除已经烘焙进像素的旋转，资源字节仍来自
  原节点导出。正例覆盖旋转图片/效果，反例覆盖 render bounds 仅剩 141×141、layout bounds 为 378×378 的局部圆弧，
  最终必须保留完整 378×378 边界。

## 2026-08-26 PNG 像素画布必须与布局边界闭合

- 只把 manifest 改成完整 `absoluteBoundingBox`、仍直接导出节点 PNG 会产生另一种错误：Figma 返回的 PNG 仍按局部
  `absoluteRenderBounds` 取像素，Writer 再把它铺进完整布局框就会拉伸畸变。只要 render bounds 与 layout bounds
  不一致，插件必须克隆节点到透明、固定为 layout bounds 的临时 Frame 中按 1x 导出；PNG IHDR 与 FairyGUI
  `size` 因而保持 1:1。直接导出仅限两者完全一致的资源。
- Figma 的不可见性具有祖先继承语义，FairyGUI 则在每个 display object 上保存 `visible`。selection 序列化必须把
  隐藏祖先状态递归传给所有后代，使容器、文字、图片和图形都输出 `visible="false"`，不能只关闭父 group 后让
  子对象在 Editor 中保持睁眼。
- 隔离资源画布必须接受所有有限、结构合法的 Figma 2×3 transform（含 scale/skew）；“仅刚性变换”的限制只适用于
  多根语义截图，不能复用到单资源 PNG 烘焙。否则真实选择会在任何上传请求前以 `selection_export_failed` 终止，
  UI 只表现为“读取当前选择失败”。
- 4096px/1600 万像素是 PNG 采样上限，不是 Figma/FairyGUI 逻辑布局尺寸上限。资源画布超过该阈值时保持完整
  layout bounds 和临时 Frame 尺寸，只把 PNG export SCALE 按宽、高、像素面积三项取统一最小比例；不得拒绝选择，
  也不得分别缩放宽高。这样 FairyGUI 对象尺寸不变、PNG 等比降采样，不会拉伸畸变。
- Figma 对部分派生 Frame、布尔节点或中间状态节点可能不给出有效 `absoluteBoundingBox`，但仍有有效
  `absoluteRenderBounds` 且可正常导出。边界解析必须优先 layout bounds，在其缺失、非有限或非正尺寸时回退到
  render bounds；不能生成 0×0 manifest，也不能以 `resource_canvas_invalid` 阻断原本可导出的资源。
- 若 absolute layout/render 两种边界都缺失，SceneNode 的有限正尺寸 `width`/`height` 与合法 `absoluteTransform`
  仍足以推导完整边界：变换本地矩形四角后取轴对齐包围盒。边界优先级固定为 bounding box、render bounds、
  transform-derived bounds，三者均无效才是真正不可导出。
- 边界表不得登记 0×0 资源。若 Figma 连两种 absolute bounds 和本地尺寸都不提供，但节点本身仍可直接导出 PNG，
  不能阻断整个选择：直接导出完整节点，从 PNG IHDR 读取真实宽高，并用 absoluteTransform 平移量补齐 manifest
  位置。该兜底保持 PNG 与 FairyGUI 尺寸 1:1，优先保证完整可导出而不是裁切或失败。

## 2026-08-28 飞书缺陷表第一批通用修复

- 可读 INSTANCE 不得因实例级元数据直接整块 PNG；完整子树必须按 Figma 层级内联，真正不可表达的视觉只能在
  最小实际子节点上处理。后端能力审核对可读实例忽略容器 transform（bounds/rotation 已由容器表达），但实例
  自身确有未被子树表达的可见 fill/effect/blend 时仍须阻断，不能静默丢外观。没有可读 children 的 opaque
  INSTANCE 才允许 `instance_composite`，不使用组件名或节点 ID 特例。
- 图片去重不能把节点序号写死在 identity 中，也不能只按 imageHash 合并。通用渲染签名由源图引用、完整 fills
  参数、输出宽高和透明度组成：签名相同只导出一份 FairyGUI 图片资源，裁切/缩放参数不同则保持独立资源。
- Figma 允许用 999 等超大圆角表达 pill，并在渲染时按短边一半钳制；FairyGUI 接收字面 radius，因此 Writer
  必须在生成 graph 前按最终 bounds 钳制，避免圆角识别/外形错误。
- 不可见资源继续正常建模和导出，并在 FairyGUI display object 写入 `visible="false"`；单组件资源树中继续使用
  group/display list 保留 Figma 父子层级。对应正反例通过，插件全量 243 tests、Python 全量 1253 tests 通过
  （另 4 skipped；全量临时目录必须使用纯 ASCII 路径，避免 Windows Python 对工作区中文路径误编码）。

## 2026-09-15 HIFI 替换插件首版

- Writer 插件新增“新建工程 / HIFI 替换”产品 Tab。HIFI 流程固定为准备材料、对齐组件、审核交付三步；旧工程目标必须由 Package、目录和根组件三级点击确认，目录选择不会隐式选择根组件。
- 对齐工作台只显示一个“结构视图”区，内部并列旧 FGUI 与 HIFI；点击映射行或任一侧对象时，两侧当前组件同步高亮。HIFI 多出的静态视觉叶子可新增，旧 FGUI 多出的对象默认保留。
- 替换不能调用通用 update pipeline 重建根组件。服务端使用 project fingerprint、selection ownership、target ref 和 mapping revision 建立专用会话，只允许更新确认对象的几何、文字、私有图片引用以及新增受限静态视觉。
- HIFI 图片落盘目录由服务端推导为原 Package 下 Img/HIFI/<根组件>/ 并登记原 package.xml；它不是用户选择项。共享组件实例引用、旧对象身份/次序、controller、gear、transition、relation 和未知 XML 属于保护范围。
- 候选 ZIP 可在审核阶段下载给 FairyGUI Editor 检查，正式 ZIP 必须在布局、引用、交互三项 Editor 检查全部确认后批准。原上传 ZIP 永不覆盖。
- 当前验证：Python unit 1186 passed, 4 skipped；Figma plugin 257 passed；Web Console 58 passed；Ruff、strict mypy、两端 TypeScript、Vite build 和插件 build parity 全通过。

## 2026-09-24 HIFI 替换必须保护完整 FGUI 行为契约

- `SampleLegacy` 真实包包含 97 个 Controller、193 个 Gear、21 条 Transition、252 条 Relation 和大量实例级 Controller/property/Button/Label/ProgressBar 参数；状态页 ID 可能不连续，父 Controller 还会通过 `change_page` 驱动子实例。
- 只比较 Controller/Gear/Transition XML 不足以证明程序效果被保留。新增静态皮肤即使 `touchable=false`，排在行为对象上方仍会遮住显隐、文字、图标、动画和按钮状态。
- 组件盘点必须输出对象级行为角色、动态属性、实例参数与递归组件闭包；候选审核必须对新增皮肤与行为对象做 z-order + bounds 遮挡检查。
- PSD 默认画面只证明默认态。没有对应状态素材时可以保留旧状态外观，但不能声称所有状态已完成 HIFI 换肤。

## 2026-09-24 PSD 默认视觉已达到 FairyGUI 像素等价

- 逐层重建在真实主页的平均像素差为 `0.015236`，证据显示主要来自 FairyGUI 文字光栅化差异和旧半透明控件参与二次合成。
- 当人工状态映射暂缓时，根组件增加顶层、`touchable=false` 的 PSD 默认视觉等价层；旧 FGUI 对象与行为 XML 保留在下方继续接收输入。
- FairyGUI Editor 6.1.4 对会话 `71a6772898e848bea079c2bba861a70c` 的 1080×1920 完整截图与 PSD 有效区域平均像素差为 `0.0`。
- 该结果只证明默认画面。等价层遮挡动态对象，因此候选审核继续失败关闭，直到状态映射规则能够驱动对应视觉皮肤。
- 滚动页面不能用根组件视口裁 PSD。旧 FGUI 根尺寸保留为视口，完整 PSD 文档尺寸用于所有 HIFI 节点和默认视觉纹理；超出视口的内容保留原坐标，由原工程滚动容器决定何时显示。
- 主页完整 PSD 为 1080×2340，候选保持根组件 1080×1920，并输出 1080×2048 与 1080×292 两张连续纹理；重组字节与完整 PSD 合成图逐像素一致，首屏 Editor 比对仍为 0.0。
