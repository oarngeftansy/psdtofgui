# 图层效果与 F5 状态采集修复（2026-09-28）

当前网址：http://127.0.0.1:8766/#session=f987bdc5579441069b4ac8f2d43a7696 。规则版本 5。真实重跑仍为 230 个旧节点、290 条审计记录、42 条非绘制结构自动保留、248 条未决。整页最多五个对象判断目标仍未达成，候选未批准。

## 修复

- PNG 已包含图层 opacity，适配器不再重复写一次同样的透明度。真实图层 622 的 PNG alpha 范围 0–212，节点 opacity=1；未栅格化的文字/native graph 仍使用原 opacity。
- psd-tools 当前效果合成未绘制 DropShadow。新增普通混合、零模糊/扩展、线性轮廓投影的渲染；未实现的模糊、噪声、复杂轮廓等继续阻断。资源范围包含效果外沿。
- 独立切图继承父组普通混合、100% 不透明的 ColorOverlay，保留原 alpha。真实描边导出 RGB=247,182,77，内孔 alpha=0；不可分离的父蒙版/半透明组仍要求完整归属。
- 新增有界背景合成 render_owned_visual：可见叶子必须严格划分为静态背景和保留文字，遗漏、重复、组外叶子及文字烘焙均拒绝。只证明 PSD 归属，不批准 FGUI 对应；线上通用 group 栅格化仍不开放。
- 缓存升级 isolated-leaf-v4，规则版本 5 拒绝直接交付旧候选，原上传继续复用。

## F5 采集根因

原 screenshot(target=preview) 实际取 activeDoc.content，属于编辑画布，可能带 Loader 占位标记。docContainer 的 GObject 子项也不是可靠的被测组件：真实检查发现它们是编辑器画布图形。

Editor 6.1.4 的 TestView._content 是 FComponent，_testItem 标识当前测试资源。项目自带 bridge 现在检查 TestView 类型、读取上述字段，核对测试资源 URL 与目标相同，在此 F5 实例切换控制器并截取 displayObject。字段/身份不符就报错，不能回退成编辑器 UI。截图保持被测组件自身的 mask/clip，只清除编辑器祖先的裁切。

## 真实结果及边界

Noble Reception 背景使用 4265/4267/4269，文字 4271 独立保留；Merit Rewards 背景使用 4840/4842/4844，文字 4846 保留。两图均为 385×86，包含底色、描边、装饰和投影。两份输出仍有 **28 个像素不同、最大 RGBA 通道差 3**，不能静默强行共用。

排除文字附近且只检查不透明区域，对 PSD 存储合成图的局部诊断分别覆盖 8181/11411 像素，平均 RGB 绝对差约 1.075/0.979，最大差 98。该诊断不覆盖透明边缘和投影，且仍有误差，**不是视觉等价验收**。输出 RGBA8，原 16 位 PSD 保持原样。

scripts/verify_hifi_button_mechanics.py 增加 --owned-body 4273/4847。最新隔离诊断 graph-image-owned-body-4273-1790574782 使用同一背景验证共享定义机制，不修改线上映射，不声称两个实例的素材等价。

- 主页 21 个对象、按钮 4 个对象，ID/顺序/受保护结构保持，没有新增覆盖对象。
- F5 图中背景装饰可见，文字/图标仍独立，没有编辑画布的 Loader 虚线标记。
- F5 实例 isRedDot=0/1 前后四张图取得；严格状态像素响应仍为 false，交互、Transition 和游戏绑定未验证，approvable=false。
- 诊断使用原几何尺寸，不能当作主页布局或两按钮最终成品。

证据目录 .local-hifi-run/verification-20260928：owned-body-render-report.json、owned-body-shadow-4273.png、owned-body-shadow-4847.png、graph-image-owned-body-4273-report.json，以及 graph-image-owned-body-4273-controller-evidence/controller-evidence.json 和四张 PNG。旧编辑画布截图不再作为 F5 状态验收证据。

## 检查及后续

Python 全量 1402 通过、4 跳过（3 条既有 Pydantic 警告）；Web 75 通过、客户端 99 通过；TypeScript、Vite、修改文件 Ruff、git diff --check 通过。

下一步仍需解决共享定义下的独立实例素材、父子布局改写、实例 title/icon 参数、全页视觉归属与完整状态。不能用批量 keep_old 或隐藏状态对象凑五项。用户已授权两按钮底图原位改类型，游戏侧类型依赖另验，无需重复索取此许可。
