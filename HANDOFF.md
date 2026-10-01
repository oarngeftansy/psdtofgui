# PSD → FGUI 原对象视觉替换：交接文档

更新日期：2026-10-01（Asia/Shanghai）

## 1. 任务目标

输入旧 FairyGUI 工程、与页面对应的 PSD，以及最终效果图。在保留旧工程全部程序逻辑的前提下，把旧对象的视觉替换成 PSD 视觉，并以 FairyGUI Editor 6.1.4 的实际页面截图验收。

目标不是把效果图切成若干块盖在旧页面上，也不是重新搭一套仅看起来相似的页面，而是：

- 继续使用旧对象、旧组件实例以及旧文字对象。
- 保留对象 ID、名称、显示列表顺序、控制器、Gear、Relation、Transition、参数和运行时绑定。
- 图片、按钮、文字等均在原对象上修改视觉；禁止在同一位置新增覆盖层。
- PSD 没有但旧工程存在的对象身份与程序关系要保留；目标状态是否继续贡献旧视觉由 `visual_disposition` 单独决定。
- 文字不能烘焙进 PNG；应直接修改旧文字对象的内容、字体、字号、颜色、描边和几何。
- 共享组件允许复制组件定义以隔离当前页面，但克隆前后内部结构必须一致。
- 所有位置、方向、尺寸和视觉样式应与参考图一致。
- 最终标准：Editor 全帧为 `1080×1920`，`mean_pixel_difference == 0.0`；未达到时不得标为完成或可批准。

## 2. 工作区和 Git 状态

- 当前工作树：`C:\Users\<user>\Documents\figma转fgui\source\.worktrees\hifi-replacement-plugin`
- 当前分支：`codex/hifi-replacement-plugin`
- 基线提交：`99a99f18ee8b0d4df0924b9d82270aad37a9a8c3`
- 当前修改尚未提交；打包内容保留了全部未提交修改。
- 本轮共修改 28 个受版本控制文件，约 `6381 insertions / 352 deletions`。
- 交接时请先执行 `git status --short`，不要清理、重置或覆盖现有修改。

主要修改文件：

- `src/figma_to_fgui/hifi_mapping.py`
- `src/figma_to_fgui/hifi_nested.py`
- `src/figma_to_fgui/hifi_patch.py`
- `src/figma_to_fgui/hifi_replacement_models.py`
- `src/figma_to_fgui/hifi_replacement_workflow.py`
- `src/figma_to_fgui/project_package.py`
- `src/figma_to_fgui/psd_effect_render.py`
- `src/figma_to_fgui/psd_intake.py`
- `src/figma_to_fgui/psd_native_graphs.py`
- `src/figma_to_fgui/psd_source_store.py`
- `src/figma_to_fgui/fairygui_editor_verify.py`
- `src/figma_to_fgui/uploaded_project.py`
- 与上述逻辑对应的 `tests/unit/test_*.py`

## 3. 当前实验输入

- 数据目录：`C:\Users\<user>\AppData\Local\Temp\hifi-live-data`
- 会话 ID：`c681e3a6020e4902a592b8c1ce7b4168`
- owner：`local-test`
- 旧工程 project ID：`634d524c84774c2c85653ab2bf23dc2b`
- 旧工程 fingerprint：`be831dbd2e7dea351364ef26da941a70cc9385a107be6f86ee80962189dcdcb5`
- PSD/source ID：`e6762c29014eef93aa5fd0e7a41e38e2b63e081566982854a744e128d71e4961`
- 目标包：`Tower`
- 目标组件：`Panel_Tower_Main`
- 组件路径：`assets/Tower/Panel/Panel_Tower_Main.xml`
- 原始 PSD：`hifi-live-data\hifi-sources\psd\e6762c29014eef93aa5fd0e7a41e38e2b63e081566982854a744e128d71e4961\source.psd`
- 参考图：`hifi-live-data\hifi-sources\psd\e6762c29014eef93aa5fd0e7a41e38e2b63e081566982854a744e128d71e4961\viewport-0-210-1080x1920.png`

完整交接 ZIP 的 `inputs/` 和 `evidence/` 目录中已复制这些文件，接手者无需依赖上述临时目录仍然存在。

## 4. 当前映射与构建流程

1. 解析旧 FGUI 对象图：对象 ID、类型、顺序、父子层级、组件引用、控制器、Gear、Relation、Transition、参数及共享定义。
2. 解析 PSD：图层树、文字图层、可见性、bounds、效果、合成参考图。
3. 依据名称、对象类型、位置、尺寸、父级和顺序，为 PSD 节点与旧 FGUI 对象评分和配对。
4. 先做外层组件映射，再进入克隆的共享组件定义做内部子对象映射。
5. 对 PSD 可见叶节点做唯一所有权划分：文字归旧文字对象，背景/图标归旧 Image 或 Loader；同一 PSD 叶节点不得被两个对象重复渲染。
6. 在原对象上应用视觉。需要隔离共享组件时只克隆组件定义，屏幕实例及组件内部结构保持不变。
7. PSD 没有对应视觉的旧对象继续保留运行时身份。静态旧 Graph 若与更高层、已映射的新视觉明确占据同一目标区域，可标记为 `visual_disposition=retire`，仅关闭目标状态的旧像素；禁止再通过缩小/移动旧 Graph 把它“塞进”新资源下面。
8. 运行结构审计，检查 displayList 的对象 ID、顺序、标签类型及程序结构是否变化。
9. 构建候选工程，在 FairyGUI Editor 6.1.4 中打开 `Panel_Tower_Main` 并截图。
10. 与参考图做全帧和区域差异。差异不为零时继续定位对象级问题，不能批准下载或覆盖。

## 5. 已实现的核心修复

- 增加映射唯一所有权和结构审计，阻止 `add_visual`、`visual_echo` 和重复 PSD 像素所有权。
- 对共享组件做结构保持型克隆，继续复用旧屏幕实例和内部对象。
- 图片/Loader 替换时清理会二次处理新 PNG 的旧颜色、对齐和缩放属性；保留非当前状态 Gear 数据。
- 文字继续使用旧文字对象；清理旧 bold/italic/stroke/tracking/faceDilate 等叠加样式后再应用 PSD 样式。
- PSD 效果渲染、实际 alpha bounds、资源几何和 Editor 校验链路已大幅补充。
- 项目打包会移除不应进入最终包的临时验证文件。
- owned visual 缓存已升级到 v14，防止继续复用此前污染的资源。
- 新增回归测试 `test_owned_visual_resource_never_copies_retained_text_from_full_composite`，证明图片资源不能从整张合成图复制保留文字或周围背景。

## 6. 两个关键候选及结论

### v13：更接近参考，但资源被合成图污染

- 候选 SHA：`e3cd4be45ddf6866c28248975882cdbe7760964d2bdb92335811ef77d9664874`
- 工程 ZIP：`evidence\v13-composite-calibrated\manual-pivot-badge-final.zip`
- Editor 截图：`evidence\v13-composite-calibrated\editor.png`
- 平均差异：`0.004357236696118777`

已确认问题：

- Challenge 的按钮 PNG 内已经包含一份 “Challenge”，同时旧 `title` Text 仍在渲染，构成明确的双层文字。
- Noble Reception 和 Merit Rewards 的 PNG 也带入了浅色文字像素，旧标题文字仍在。
- 返回按钮 PNG 带入了周围背景/脏 alpha 边。
- 根因是 `owned_visual_resource` 在局部渲染后，又从 PSD 的整张 `composite.png` 按空间区域复制/校准像素；保留对象遮罩不够准确，导致文字、背景和邻近像素被烘进组件资源。

这个版本不能恢复为正式方案。不要为了缩小数值差异重新引入整张合成图裁切。

### v14：去掉合成图复制，资源干净，但 PSD 直接渲染不忠实

- 当前候选 SHA：`bb6acdece0bac51bd07fad2b2fd4b34273a44f3ce42274849d46126121ca6f43`
- 工程 ZIP：`evidence\v14-direct-layer\candidate.zip`
- Editor 截图：`evidence\v14-direct-layer\editor.png`
- Editor：6.1.4，完整 `1080×1920`
- 平均差异：`0.007802499394819656`
- `approval_ready=False`

已确认改进：

- Noble、Merit 和 Challenge 的图片资源中不再含有标题文字。
- 组件 XML 中保留的仍是唯一一份旧标题 Text，没有再新增文字对象。

仍然存在的问题：

- Challenge 的直接 PSD 图层渲染出现不正确的浅色内部竖条/半圆几何，与 PSD 的最终合成外观不一致。
- Noble、Merit、返回按钮的边缘、alpha、效果和尺寸仍像贴上去的资源，用户直观看到类似双层/脏边的效果。
- Noble/Merit 旧组件定义画布仍为 `375×77`，PSD 父组约为 `375×72`；内部 Loader 仍沿用旧 `374×77` 几何，而 PSD 可见叶约为 `371×72`。旧组件画布与新资源边界未同步，是拉伸和外框感的重要来源。
- v14 总体数值反而比 v13 更差，说明当前 `psd-tools` 局部/group 合成无法忠实再现 PSD 内嵌的最终合成预览。

## 7. 已证实的根因

1. **整图像素回填会制造伪替换。** 从 PSD merged composite 或效果图按矩形复制像素，会把原本应由旧 Text/其他旧对象渲染的内容烘进 PNG，造成新旧叠加。
2. **`psd-tools` 的局部/group 合成与 Photoshop 最终外观不完全一致。** 已测试 `document.composite(layer_filter=...)`、`group.composite()`，以及临时隐藏文字后再 `document.composite()`；Challenge 都产生了相同的错误浅色内部形状。
3. **资源边界与旧组件/Loader 几何没有统一。** 即使 PNG 内容不再包含文字，旧画布、旧尺寸、pivot/anchor、缩放和 PSD alpha bbox 不一致，仍会让边缘像两层框。
4. **“页面上没有第二个 Image 对象”不等于视觉没有叠加。** 一张被污染或错误合成的 PNG 本身就可能携带第二份文字、背景或旧边缘。
5. **FairyGUI 最终仍需要 PNG 资源。** “直接从 PSD 导出”应理解为只导出被映射的 PSD 图层/组，保留透明背景并排除文字/邻近对象；不能把整张效果图裁片当资源。

当前机器未发现 Photoshop、ImageMagick、Krita 或 GIMP，只有 Windows 自带的 `convert.exe`，因此尚无可直接替代 `psd-tools` 的 Photoshop 忠实渲染器。

## 8. 下一步建议（优先级顺序）

1. **先解决忠实 PSD 局部导出。** 寻找或实现能够准确处理组蒙版、剪贴、图层样式、混合模式、ICC 和 alpha 的渲染路径；导出时应隐藏归属于旧 Text/其他旧对象的 PSD 叶节点，但绝不能从 merged composite 回填空间像素。
2. **统一三套边界。** 对每个映射项显式记录 PSD 组 bounds、实际 alpha bbox 和 FGUI 对象/组件本地坐标；组件定义画布、内部 Loader 和屏幕实例的几何必须用同一换算，不再只换资源不换画布。
3. **保持一对象一视觉所有权。** 构建前检查 PNG 内是否出现由保留文字或邻近对象拥有的像素；检查相同区域是否有多个默认可见旧对象共同渲染。
4. **按对象做 Editor 闭环。** 先验证返回按钮、Noble、Merit、Challenge 四个区域；每次只改变一个映射/渲染因素，并保存区域 diff 和 alpha bbox。
5. **文字只调整旧 Text。** 用户曾表示部分字体像素差异可以暂时不管，但明确指出右下角 Challenge 文字仍不对；禁止用文字图片绕过。
6. **本页达标后再换另一个非 `Panel_Tower_Main` 页面回归。** 新页面必须使用另一份对应 PSD/FGUI 组件，验证方案不是针对当前页面硬编码。

## 9. 明确禁止的“修复”

- 不得新增显示对象盖住旧对象。
- 不得删除旧对象、改 ID、改类型或破坏 Controller/Gear/Relation/Transition。只有经过映射判定的 `visual_disposition=retire` 可以关闭其默认目标状态视觉；这不是删除对象。
- 不得把文字烘焙进按钮 PNG，同时保留旧 Text。
- 不得从整张效果图或 PSD merged composite 简单裁切/补像素来追求更小 diff。
- 不得只看结构 XML 判断无重叠；必须检查实际 PNG 内容与 Editor 截图。
- 不得因为候选能打开或整体看起来接近就声称完成。

## 10. 测试状态

- 最近一次完整测试（v14 最后一处回归测试加入前）：`1544 passed, 4 skipped, 8 warnings in 106.17s`。
- 最后一处改动后相关测试：`tests/unit/test_psd_owned_visual_store.py` 为 `12 passed, 6 warnings`。
- 最后一处改动后尚未重新跑完整测试，接手者应首先补跑。

建议命令：

```powershell
cd 'C:\Users\<user>\Documents\figma转fgui\source\.worktrees\hifi-replacement-plugin'
git status --short
$env:PYTHONPATH='src'
python -m pytest tests/unit/test_psd_owned_visual_store.py -q
python -m pytest -q
```

## 11. 交接包目录说明

- `project/`：当前完整源码工作树，包含 `HANDOFF.md` 与所有未提交修改。
- `inputs/source.psd`：本次原始 PSD。
- `inputs/reference-1080x1920.png`：PSD 对应效果图。
- `inputs/old-fgui-project/`：本次原始旧 FGUI 工程。
- `evidence/v13-composite-calibrated/`：更接近参考、但已确认有合成图污染的候选和截图。
- `evidence/v14-direct-layer/`：当前干净所有权、但 PSD 局部渲染与几何仍错误的候选和截图。
- `evidence/user-comparisons/`：用户给出的初始对比和局部问题截图。

为了避免把不可移植或可重建内容塞入交接包，`project/` 未复制 `.git` 指针、`node_modules`、Python/pytest/ruff/mypy 缓存、`.qa` 的重复工程，以及 `.local-hifi-run` 下约 5.8 GB 的重复运行工程、虚拟环境和验证副本。关键原始输入、当前/历史候选、Editor 截图和直接 PSD 导出诊断均已单独收入交接包；源码、测试、配置、文档、锁文件和未提交修改均保留。

## 12. 完成前检查表

- [ ] 没有新增可渲染显示对象。
- [ ] 原组件与克隆组件 displayList 的对象 ID、顺序、标签类型一致。
- [ ] 控制器、Gear、Relation、Transition 和参数未丢失。
- [ ] 每个 PSD 可见叶节点只有一个旧对象拥有。
- [ ] 所有图片资源不包含保留文字、邻近对象或效果图背景。
- [ ] 所有文字由原 Text 对象渲染。
- [ ] 页面中没有新旧按钮、文字或边框的视觉重叠；被目标视觉替代的旧静态 Graph 已进入 `visual_disposition=retire`。
- [ ] Editor 6.1.4 实际打开目标组件并成功截取完整 `1080×1920`。
- [ ] `mean_pixel_difference == 0.0`。
- [ ] 换另一份 PSD 和另一目标组件回归，仍满足上述约束。

当前结论：**尚未完成，不可批准。** 当前最核心的阻塞不是再加规则，而是获得“不含其他对象、同时与 Photoshop 最终合成一致”的 PSD 局部视觉，并把它的真实边界正确换算到原 FGUI 对象和组件画布。


## 13. 2026-10-01 修复补充（policy revision 25）

本次接手后已完成两项结构性修复：

1. **运行时身份与视觉贡献分离。** `HifiMappingItem` 新增 `visual_disposition`。PSD 模式下，旧对象仍保留 ID、类型、控制器、Gear、Relation、Transition 和实例参数；对于被同层级新视觉明确替代的静态旧 Graph，映射会标记为 `retire`，构建时仅关闭默认目标状态像素。该机制用于解决 Noble/Merit 旧黄色 Graph 残留，替代原先“把旧 Graph 缩进新按钮不透明区”的做法。
2. **逻辑尺寸与视觉尺寸分离。** `HifiMappingItem` 新增 `logical_bounds_policy`，默认 `preserve`。PSD group/raster bounds 不再自动改写旧 Component 的逻辑尺寸；组件内部 Loader/Image/Text 可以按 PSD 视觉范围变化或溢出，但旧 Component 的布局/交互尺寸默认保持。只有显式 `resize` 才允许同步 definition size。

同时将 mapping policy 升级为 **25**，Store/Workflow/Review 使用同一常量校验，避免策略版本漂移。

当前环境无法安装 `psd-tools`，因此不能在本机重跑依赖 PSD 解析的完整 1500+ 测试，也没有 FairyGUI Editor 6.1.4 可做最终截图闭环。已完成 Python 编译检查，并对不依赖 `psd-tools` 的 HIFI 核心测试集执行回归：**145 passed**。

仍未解决的主要阻塞保持不变：`psd-tools` 对 Challenge 等复杂组效果的局部合成与 Photoshop 最终外观不一致。不要重新引入 merged composite 矩形裁切或整图回填。下一步应继续实现/接入忠实的组件级 PSD RenderSet 后端，并在 Windows + FairyGUI Editor 6.1.4 环境重新生成候选做区域回归。
