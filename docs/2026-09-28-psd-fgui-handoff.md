# PSD → FairyGUI 本地替换应用交接（2026-09-28）

> 最新通用规则审计见[PSD → FGUI 通用规则审计](2026-09-28-general-rules-audit.md)：规则版本 15；真实 RAR/PSD 仍有 111 条未决，其中 18 条旧对象为默认隐藏状态，候选未批准。当前会话 http://127.0.0.1:8775/#session=5b7c465e27504c1c908d5f063ae0e6c7 。下方旧版本网址和数量均为历史记录。

> 最新状态见[视口与实例素材复核](2026-09-28-viewport-and-instance-audit.md)：规则版本 7，真实材料 247 条未决。标准 FGUI 字段误报及 PSD 视口错位已修，Delegate/Reward 两份素材的原位组件变体已在 Editor 隔离验证，但全页归属、新状态视觉和验收仍未完成，候选未批准。当前网址 http://127.0.0.1:8769/#session=2c10d353ceb340ea9a5b3f8c9085a8bd 。下方版本 5 及更早内容均为历史状态。

> 最新状态见[图层效果与 F5 状态采集修复](2026-09-28-effects-and-runtime-progress.md)：规则版本 5，修复重复透明度、投影遗漏、父组叠色及编辑画布误当运行截图的问题。真实 F5 原位机制已验证；仍有 248 条未决，完整视觉和状态未通过，候选未批准。当前网址 http://127.0.0.1:8766/#session=f987bdc5579441069b4ac8f2d43a7696 。以下版本 4 及更早内容均为历史状态。

> 历史版本 4 状态见[描边与非绘制结构修复](2026-09-28-stroke-and-structure-progress.md)：规则版本 4，42 个非绘制 group 自动保留，290 条审计记录中 **248 条仍未决**；真实描边透明内孔已在 Editor 验证，完整视觉和状态仍未通过，候选未批准。当前网址 http://127.0.0.1:8766/#session=5b66ccc186254f5699726e2a9f5a6b76 。下面版本 3 及更早描述均为历史状态。

> 历史版本 3 状态见[嵌套写入与原位转换进展](2026-09-28-nested-in-place-progress.md)。用户已允许 Delegate/Reward 底图原位改类型，游戏侧类型依赖另验；原位机制及红点隔离状态已用真实 Editor 验证。递归盘点 230 个旧节点，当前 290 条未决映射，整页最多 5 个判断目标仍未达成，候选不得批准。下文 103/16 等数量为历史范围，不能代表当前结果。当前网址 http://127.0.0.1:8766/#session=60a61cd9efb94ce58258ac87e7001b1c 。

## 用户目标和不可妥协的约束

输入为旧 FairyGUI 工程（ZIP/RAR/7z/tar 等压缩包）和 HIFI PSD。用户在旧工程中指定目录与根组件；应用盘点 PSD 与旧 FGUI 的对象，完成一对一映射，只替换视觉，生成候选工程，经差异和 FairyGUI Editor 检查后交付。

- 保留旧工程中对象的身份、层级、Controller、按钮状态、Transition、动画、实例参数及程序绑定。不能用整张 PSD 效果图或新建覆盖层假装完成替换。
- PSD 所有可见图层都要保留；隐藏图层不显示。超出画布的可见内容可能用于滚动，不能裁掉。
- 设计目标是最终渲染画面与 PSD 合成图一致，同时保留旧工程的程序行为。
- 人工审核上限是**整页最多 5 个逐对象判断**，不是 5 个区域包或每页显示 5 个。超过上限时必须继续改进自动映射或明确阻断，不能暗中把其余对象认定为通过。
- PSD 图层未找到旧对象时，应优先提供“映射到现有旧对象”；只有能证明是独立新增视觉且明确归属时才允许新增。当前 PSD 本地流程不提供直接新增按钮。

这些是用户在会话中的明确要求，不是附件文档的指令。用户此前表示映射细则还需与需求方对齐；在未得到细则前，不要用未经证实的启发式把不确定映射自动批准。

## 代码与运行位置

- Git 工作树：`C:\Users\<user>\Documents\figma转fgui\source\.worktrees\hifi-replacement-plugin`
- 分支：`codex/hifi-replacement-plugin`
- 最新功能实现提交：`19a8368 Clarify PSD mapping and enforce five-object review budget`
- 前一提交：`ecbcb1c Fix archive upload gating and surface project errors`
- 交接文档已提交；交接时工作树干净。
- 当前本地服务：`http://127.0.0.1:8766/`，仅本机可访问；服务是本次会话手动启动的，不保证新会话时仍在运行。
- 启动脚本：[Start-Hifi-Replacer.ps1](../Start-Hifi-Replacer.ps1)。它启动浏览器界面；当前**没有可交付的桌面 `.exe`**。此前曾用 Python WebView2 壳展示桌面窗口，但未打包成 exe。不能再告诉用户 exe 已存在。
- 当前临时运行数据：`C:\Users\<user>\Documents\figma转fgui\local-run\current-source\data`。其中有真实上传后的工程和 PSD；不要随意删除。

## 真实测试材料

- 旧工程：`C:\Users\<user>\Downloads\HIFI_Replace.rar`（真实 RAR，约 113 MB）
- HIFI 主页 PSD：`D:\SampleDesign\SampleDesign\PSD\SampleHomePage.psd`（约 266 MB）
- 本次试验选择：`Tower → Panel → Panel_Tower_Main`。
- FairyGUI Editor 6.1.4 已在 `C:\Users\<user>\Downloads\FairyGUI-Editor_6.1.4\FairyGUI-Editor\FairyGUI-Editor.exe`；此前“机器上未检测到 Editor”的旧结论已经过时，后续应实测能否使用。
- 用户的映射问题截图：`C:\Users\<user>\AppData\Local\Temp\codex-clipboard-03496d5d-ffbd-48b2-a0ab-5c7b5a52700b.png`；“允许新增视觉节点”截图：`C:\Users\<user>\AppData\Local\Temp\codex-clipboard-f0ac92b0-1009-4693-8514-172236ed07da.png`。

## 现状与验证

1. RAR 导入已修：按扩展名选择解析器，解析器校验实际内容，不依赖 Windows/WebView2 不稳定的 MIME 标签。旧工程导入错误会贴近文件选择框显示，底部提示缺少的材料或根组件。
2. 用真实 RAR + 主页 PSD 走过浏览器端到端：工程上传和目标树返回 201/200，PSD 上传返回 201，选 `Panel_Tower_Main` 后“进入盘点与映射”解锁，映射会话创建返回 201，工作台可见。
3. 映射引擎之前仅将 `matched` 占用 PSD 节点，导致待确认的同一 PSD 节点再次成为“HIFI 新增”；现改为暂定映射也占用节点，并把完全未匹配的 PSD 子树合并为一个候选分支。真实材料从 **96 项总数、80 项待确认**降为 **32 项总数、16 项待判断**。这并不意味着 16 项自动正确，也未达到 5 项上限。
4. 工作台 HIFI 侧现在显示真实 PSD 合成图并高亮当前图层；旧 FGUI 的图片对象显示原资源缩略图（真实样本 `n22` 可显示）。组件实例目前只有对象结构和类型，没有真实 Editor 渲染图，仍不足以让人可靠确认全部映射。
5. 未配对 PSD 项提供“对应的旧 FGUI 对象 → 建立一对一对应”；PSD 本地流程不再显示“允许新增视觉节点”。当待处理项 >5，UI只展示前 5 个**诊断示例**，禁用逐项批准和候选生成，并明示自动映射未达标。不是把 16 项伪装成 5 项。
6. 相关测试：`tests/unit/test_hifi_mapping.py tests/unit/test_hifi_replacement_api.py tests/unit/test_hifi_patch.py` 共 25 通过；Web `App.test.tsx` 与 `HifiMappingPanel.test.tsx` 共 10 通过；插件客户端 `project-client.test.ts` 93 通过；Web build 通过。真实浏览器端到端曾验证新的 PSD 与旧图片预览均加载，映射 16 项；最后一次前端构建在提交 `19a8368` 后，服务仍能返回 HTTP 200。

## 尚未解决的关键问题（下一会话优先）

1. **真正把人工逐对象判断降到 ≤5，且不能牺牲准确度。** 当前 16 项中的 5 个旧对象暂定配对、7 个未对应 PSD 分支、4 个受限分支；还有 16 个旧对象被自动 `keep_old`。应先检查这些 `keep_old` 是否实际上需要视觉替换，不能只看 `unresolved_count`。需要旧 FGUI 组件实例的真实渲染证据、PSD 分层/层级关系和全局一对一分配；对不确定的关系保持阻断。
2. **禁止“新增皮肤覆盖层”冒充替换。** `src/figma_to_fgui/hifi_patch.py` 中 `build_hifi_change_bundle` 对映射到非 image 旧对象且带资源的 PSD 节点，仍可能调用 `_new_visual(...)` 并 append 到 `displayList`；测试 `tests/unit/test_hifi_patch.py::test_mapped_graph_keeps_program_object_and_adds_raster_skin` 甚至把这种行为作为预期。这与用户“只替换视觉、保留原组件与程序状态、不叠图”的要求冲突。应设计原位替换或改写被引用组件的视觉子结构；做不到就阻断，不可交付覆盖层候选。
3. **真实旧组件预览与最终 Editor 像素验证。** 目前旧侧只有图片对象有资源缩略图，组件/文字/graph 是结构框；应增加可信的 FairyGUI Editor 渲染或等价预览，并在候选工程中验证默认态及受影响状态。当前 PSD 仍有 5 类无损证据待闭合，不能宣称像素等价或正式交付。
4. **桌面 exe。** 用户曾明确要桌面应用 `.exe`，现在只有 PowerShell 启动脚本和临时 Python 壳；若后续交付应用，需真正打包并在新设备做冷启动与真实输入冒烟测试。

## 主要代码入口

- 映射判定与决策：`src/figma_to_fgui/hifi_mapping.py`
- 映射模型：`src/figma_to_fgui/hifi_replacement_models.py`
- 候选工程改写：`src/figma_to_fgui/hifi_patch.py`
- 本地应用流程：`apps/web-console/src/App.tsx`
- 映射工作台：`apps/web-console/src/figma/HifiMappingPanel.tsx`
- 前端 API 客户端：`apps/figma-plugin/src/project-client.ts`
- 本地 API：`src/figma_to_fgui/api.py`

## 新会话开始时建议的第一步

先读本文件和上述代码，并运行 `git status --short` 确认状态。不要引用旧会话的“70% 完成”“能无损交付”等未证实说法。先复现真实样本的 16 项与旧组件缺少可视预览，再确定自动映射及原位替换如何实现；完成后用真实 RAR、PSD、FairyGUI Editor 6.1.4 验证候选工程，而不只跑单元测试。

## 2026-09-28 后续实测更新（以此节数字为准）

以上“16 项”“旧组件预览未实现”等为最初交接快照，不能当作当前状态。之后展开嵌套组件、逐层审计真实 PSD，并发现重复实例会使审计记录数上升。当前规则版本 13 已实现显式多叶视觉归属、经批准的 graph 原位改 image、按 PSD 色彩生成共享组件的状态视觉、以及已展开空壳组件的结构保留。真实 RAR/PSD 新建的服务会话是 `73f81765f9c3492880bc763f925e8507`，网址 `http://127.0.0.1:8774/#session=73f81765f9c3492880bc763f925e8507`，返回 **127 条未决**：41 旧对象无 PSD 对应、63 PSD 叶层未归属、15 低置信、5 容器阻断、3 建议项。108 个共享状态实例已关联 7 个新 PNG 资源；Delegate/Reward 两组多叶按钮归属保持在现有 graph 对象上，没有新增覆盖层。

这些只是映射规则进展，**还没有候选工程，更没有 FairyGUI Editor 完整视觉/状态验收或用户批准**。41 个旧对象里包括带静态旧 URL 的 loader，不能以“运行时占位”自动保留旧视觉。详情和验证见 `docs/2026-09-28-general-rules-audit.md`。版本 12 完整 Python 测试为 `1419 passed, 4 skipped`，版本 13 针对性测试 24 通过；Web 75、插件客户端 99 通过，插件 TS 类型检查通过。下一步应继续解决静态 loader 与 PSD 图层的归属、共享实例变体写入集成，以及 Editor 全状态对比；任何无法证实的关系保持阻断。
