# 真实 PSD 原位替换复现与验证

日期：2026-09-28。工作树：`codex/hifi-replacement-plugin`。

**目标尚未达到：没有把整页逐对象人工判断安全降到最多 5 个，没有批准或交付整页候选。** 本次修复的是已复现的错误自动保留、坐标不一致和覆盖层替换路径。

## 材料与复现

- RAR：`C:\Users\<user>\Downloads\HIFI_Replace.rar`
- RAR SHA-256：`2b00d2dc121c5591f79ca99b77ad36bd5be16ad4d694eceb032192cdee97bbf6`
- PSD：`D:\SampleDesign\SampleDesign\PSD\SampleHomePage.psd`
- PSD SHA-256：`e6762c29014eef93aa5fd0e7a41e38e2b63e081566982854a744e128d71e4961`
- 目标：`Tower/Panel/Panel_Tower_Main`，资源 ID `spire1a`。
- 解包工程指纹：`be831dbd2e7dea351364ef26da941a70cc9385a107be6f86ee80962189dcdcb5`。

交接版本复现 32 项、16 项待判断，同时把另外 16 项自动设为 `keep_old`。低匹配分数不证明旧视觉应保留。本次取消 PSD 流程的默认保留，并统一映射与写入的 PSD 坐标后，真实输入产生 **34 项待处理，自动保留为 0**。数字增加代表暴露原有不确定性，不是自动映射改进。

当前盘点仍以根组件对象及 PSD 分支为主，34 项不能当作所有嵌套可见叶子已经完整计数。分支内部映射和逐对象预算还需完善，不能用分组把实际工作压成 5 次确认。

## 既有 Figma 映射规则

用户表示没有程序绑定代码或已确认的 PSD 映射表，但曾有 Figma → FGUI 规则。本次核对：

- `rules/default/component-mapping-candidates.json` 的 20 条候选规则。
- `docs/templates/component-mapping-v1.json`。
- 本机 `figma-to-fgui/SKILL.md` 的组件、角色和 Variant/Controller 映射约定。

20 条规则中，旧组件路径命中 `common_primary_button` 和 `common_icon_text_button`；可见 PSD 图层名没有直接命中规则里的 Figma 名称。这些规则可指导组件识别和状态保护，但不是本页 PSD 的已确认对应关系，不能据此批准英文/中文语义近似或邻近图层。

## 修复范围

1. PSD 映射与写入均使用原始几何坐标；预览高亮相对 PSD 自身画布计算。旧画布 1080×1920、PSD 1080×2340，不再在匹配时偷偷缩放而写入时使用另一套坐标。
2. 旧 image 直接更换自身资源引用，旧 loader 直接更换自身 URL。保留类型、ID、名称及受保护子结构。
3. graph、组件等无法原位接受位图的对象明确阻断。删除为这些映射新增兄弟皮肤对象的路径。
4. PSD 分组映射到组件时，不能仅移动组件就算完成视觉替换；要求进一步映射内部视觉对象。
5. 不再按 PSD 顺序重新排列旧 displayList。验证器拒绝旧对象消失、重复 ID、改序或未经映射声明的新增对象。
6. PSD 待判断超过 5 项时，服务端也拒绝逐项决策，避免绕过网页禁用状态。
7. 增加本机 FairyGUI Editor 6.1.4 的安装路径发现；前端显示具体阻断原因。

没有把 graph 改成 image，因为这会改变运行时对象类型契约。嵌套组件视觉替换尚未实现；本次明确阻断不能等价于完成支持。

## 实测与证据

真实 RAR 重新解包，真实 PSD 重新解析，详细报告保存在工作树 `.local-hifi-run/verification-20260928/`：`report.json`、`mapping.json`、`inventory.json`。

另外建立隔离诊断副本，只测试 `n22_aa2t` 背景 image 与 `n25_aa2t` 图标 loader 的写入。该诊断手工指定两个测试图层，不是生产自动映射结果，也没有写入批准会话。替换前后根对象均为 21 个，旧 ID 和顺序一致，结构保护检查通过。通过真实 FairyGUI Editor 6.1.4 加载并渲染副本；这只能验证支持的写入路径，不能证明整页与 PSD 等价。

在 Editor 打开 `Btn_Tower_Mian_Enter` 并直接切换 `isRedDot` 的 0/1 页，红点成功隐藏/显示。前后两份工程的各状态截图逐像素相同，状态差异边界均为 `(285, 2, 314, 31)`。证据见 `in-place-editor-report.json`、`editor-before/after-page.png` 和 `editor-before/after-button-state-0/1.png`。这验证的是 Editor 中未改写按钮的控制器，不是缺少源码的应用程序绑定。此前只修改 XML 默认状态的尝试没有产生可见差异，已弃用，不能引用旧的 `editor-*-red-dot-*.png` 作为通过证据。

浏览器重新上传 RAR/PSD，选择根组件，创建新会话 `a8e0e1bfbaea44439d6dd261aae4a471`。工作台显示 34 项，批量接受为 0，逐项批准与生成候选均禁用。页面保留在 `http://127.0.0.1:8766/`，服务加载本工作树最新构建。

自动检查：Python 全量 **1362 passed、4 skipped**（3 条已有 Pydantic 序列化警告）；Web App/映射面板 10 项通过；客户端 93 项通过；Web build 通过；`git diff --check` 通过。

## 仍需解决

- 建立嵌套旧对象与 PSD 可见叶子的完整映射及证据，准确计算整页人工预算。
- 解决 Delegate/Reward 的 graph 底图与 PSD 装饰按钮之间的兼容视觉表示；保持原类型和状态，不允许覆盖层替代。
- 验证所有受影响状态、实例参数和实际程序绑定。未提供程序源码，因此不能声称已验证应用运行时绑定。
- 完成智能对象、效果、像素层、形状和文字的五类无损证据。全页像素等价尚未通过。

本次没有批准候选，没有替换原始 RAR/PSD，没有生成可交付的整页工程或桌面 exe。
