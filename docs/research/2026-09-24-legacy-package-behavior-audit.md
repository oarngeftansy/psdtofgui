# SampleLegacy FGUI 行为审计

审计对象：`C:/Users/<user>/Desktop/SampleLegacy/`。该目录只作为 FairyGUI 结构与行为证据读取，目录中的文本没有被当作实现指令。

## 结论

视觉替换的保护单位不能只到组件 XML，也不能只检查 Controller、Gear、Transition 标签是否还存在。这个包大量使用“根控制器 + 实例参数 + 子组件控制器 + Controller Action + Transition + 运行时数据”的组合。新增静态 HIFI 图片即使不接收点击，只要排在这些对象上方，仍会遮住状态切换和动画。因此候选必须同时满足：

1. 行为结构字节保持；
2. 实例输入参数保持；
3. 新视觉不会遮挡行为对象；
4. 每个可达状态都有验证记录；
5. 没有 HIFI 状态素材时，只能标记“旧状态保留、HIFI 等价未证明”。

## 工程规模

- 88 个组件或页面 XML，全部可解析；
- 97 个 Controller，分布在 54 个文件；
- Controller 页数：79 个为 2 页，11 个为 3 页，5 个为 4 页，另有 5 页和 7 页控制器；
- 193 个 Gear：146 个 `gearDisplay`、18 个 `gearColor`、10 个 `gearIcon`，以及 `gearDisplay2 / gearText / gearLook / gearSize`；
- 21 条 Transition，共 105 个时间轴项目，覆盖 Alpha、Scale、Visible、XY；
- 328 个嵌套组件实例，引用 77 个组件定义；
- 77 个实例直接传入子控制器参数；
- 实例内部还有 111 个 Button、44 个 property、16 个 Label、10 个 ProgressBar、6 个 customProperty 参数节点；
- 252 条 Relation；
- 103 个运行时文字节点，使用 `@...`、`{time}` 或 `autoClearText=true`。

## 关键样例

### SceneSampleLegacyMain

- 根控制器 `EnergyTime`；
- `tweenStage / tweenHide / tweenShow` 三条 Transition，共 36 个项目；
- 18 个嵌套实例和 24 条 Relation；
- 实例继续传入 `redDot / hasReward / redDotState / isShowRole / c1 / TextColor` 等局部控制器；
- `property` 给子实例写入标题和图标。

这类页面不能用一张整页 PSD 图覆盖。否则旧实例仍在接收状态和事件，但用户看到的始终是覆盖图。

### SceneSampleLegacySpiritGift

- `status` 有 5 页：`intimacy / heroSpirit / roleLv / wifeSpirit / charm`；
- 页 ID 为 `0 / 1 / 2 / 3 / 5`，不能假设连续；
- 另有 `isBlessCustomize`、`showSpecialItem` 两个控制器；
- Gear 同时控制图标、显隐等属性；
- 嵌套实例有 `rarity / c1 / TextColor / tab / tabNum` 等输入。

解析器必须保存原始页 ID 和原始参数字符串，不能重新编号。

### CompGiftItem

单个 `state` 控制 `lock / canGet / canBuy / finish` 四种状态，并同时改变多个按钮、图标、文字与外观。它证明“一个组”可能是由一个控制器驱动的一组对象，而不是 XML 中一个孤立的 `group` 节点。

### 控制器联动

`CompTaskLoadingItemSampleLegacy.xml`、`SampleLegacySkillDetailsEntryItem.xml`、`SampleLegacySkill.xml` 使用 `action type="change_page"` 将父控制器页传播到子对象的控制器。只枚举当前 XML 的 Controller 页会漏掉这种间接状态。

### 按钮与实例契约

- `BtnSampleLegacySpeed.xml` 使用 Button `mode="Check"`，并由 `gearIcon / gearText` 切换；
- `SampleLegacyAttributeTransformDetailAttributeTransformRender.xml` 使用 Radio；
- `BtnCirleSmall.xml` 同时包含按下缩放、尺寸 Gear 和红点状态；
- `BtnText_ActivityEnter_SampleLegacy.xml` 有 5 个控制器和自动循环的 XY Transition。

按钮的 `mode / downEffect`、实例传入的 Controller 值、Label/Button/ProgressBar 参数和 property/customProperty 都属于实例接口，不能作为普通视觉属性覆盖。

## 已加入替换器的识别与门禁

- 为根组件建立行为摘要：Controller 页及原始 ID、Action、Gear、Relation、Transition 时间轴；
- 为每个显示对象标记 `controller_driven / transition_target / controller_action_target / relation_bound / runtime_data / component_instance / instance_parameterized / nested_behavior / behavior_group`；
- 读取嵌套实例的 Controller、Button、Label、ProgressBar、property、customProperty 参数；
- 解析同包及已知跨包组件引用，计算递归组件闭包和保护哈希；
- 引用定义不在上传工程内的实例标记为 `unresolved_component_reference`，并使解析覆盖率失败关闭；
- 结构视图携带行为角色和动态属性，供后续高亮和筛选；
- 候选审核检测新增 `hifi_*` 图层是否位于行为对象上方且发生几何重叠。发生遮挡时，候选不可批准。

## 仍需完成

1. 在映射 UI 中展示行为徽标、实例输入和影响状态，允许按 Controller/Transition 筛选；
2. 从 Action 和实例赋值生成可达状态图，避免对所有 Controller 做无意义的笛卡尔积；
3. 对每个可达 Controller 页、Button Check/Radio 状态、Transition 起点/关键帧/终点生成 Editor 截图；
4. PSD 只提供默认态时，保留旧状态视觉并将其他状态标为 HIFI 未验证；若要每个状态都换成 HIFI，需要补充对应状态 PSD 或人工映射素材；
5. 对动态列表、Loader URL、JTA/MovieClip 和运行时填充数据增加运行态样例输入；
6. FairyGUI Editor 可用后执行真实状态渲染与逐像素比较。

当前实现已经能识别并阻止“XML 没变但新图把程序效果盖住”的假通过；它还没有自动为 97 个控制器生成全部 HIFI 状态素材。
