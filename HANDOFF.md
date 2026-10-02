# PSD → FairyGUI Semantic Reskin — Current Handoff

更新日期：2026-10-02

> **Authoritative policy: Policy 27.**
> 本文件描述当前生产约束。Policy 25 / Policy 26 的说明仅属于历史实现，不再作为当前行为依据。

## 1. 目标

输入：旧 FairyGUI 工程、目标 PSD、参考效果图。

输出：在 **保留旧 FairyGUI 程序契约** 的前提下，把目标页面/组件换成 PSD 视觉，并以 FairyGUI Editor 6.1.4 实际渲染作为最终验收。

参考效果图只用于像素验证，不允许作为裁图、补图、回填资源来源。

## 2. Policy 27 默认模型

```text
Old FGUI Semantic Component
├─ Runtime Contract          保留
│  ├─ object/component identity
│  ├─ name / id / path
│  ├─ Controller
│  ├─ Gear
│  ├─ Relation
│  ├─ Transition
│  └─ instance/runtime bindings
├─ Native Text               原对象重样式
├─ Nested Component          独立语义边界
└─ Legacy Visual Bundle      当前状态视觉可被替换

                 ↓ semantic pair

PSD Semantic Group
├─ Native Text evidence
└─ Target Visual Bundle
   ├─ background
   ├─ border
   ├─ icon
   ├─ ornament
   ├─ mask/effect output
   └─ other visual leaves
```

核心原则：**Component 保留，Visual Bundle 整体换皮。**

## 3. Mapping 规则

1. 先做 `FGUI Component ↔ PSD Group` 语义配对，再处理 leaf。
2. matched Component 在业务语义上属于 `MODIFY`，不是 `KEEP + ADD`。
3. PSD Group 内的 shape/pixel/smart-object leaf 是视觉实现细节，不是独立业务 UI。
4. matched Group 内默认不允许业务级 `ADD`；无法被现有视觉 host 安全吸收时必须 `BLOCK`。
5. Text/RichText 继续使用旧 FGUI Text 对象；PSD 文字只提供内容、样式和几何证据。
6. Nested Component 不被父级 Visual Bundle 展平；它保持自己的 runtime/state contract。
7. 一个 PSD visual leaf 只能有一个 render owner；重复 ownership 必须阻止候选生成。

## 4. Target-State Visual Ownership

旧对象身份与旧像素贡献是两件事。

- `preserve`：当前状态继续由该对象承担目标视觉。
- `retire`：对象和逻辑仍存在，但静态旧皮不再贡献目标状态像素。
- `other_state`：当前运行时初始状态不显示；保留给其他 Controller 状态。
- `structural`：结构对象保留，不作为独立绘制视觉。

只允许对**静态、非状态驱动、非 runtime visual** 的旧 Graph/Image 做自动 target-state retirement。

对于当前状态仍可见且受 Controller/Gear/Transition/runtime 控制、但没有 PSD ownership 的旧视觉，必须 `BLOCK`；禁止用“继续 KEEP”或全局隐藏来掩盖冲突。

## 5. Runtime Contract 硬约束

默认不得修改：

- Object / Component identity
- event-facing `id` / `name` / hierarchy path
- Controller 定义和页面结构
- Gear 绑定
- Relation
- Transition
- runtime / instance parameters
- nested component reference contract

逻辑 component 的 logical bounds 默认保持；只有经过明确审计才能使用 `logical_bounds_policy=resize`。

## 6. PSD ownership 的唯一权威

Policy 27 的 `normalize_psd_semantic_reskin()` 是 PSD Semantic Group / Visual Bundle ownership 的唯一权威层。

旧 `build_mapping()` 中仍可能存在用于历史兼容或非语义流程的 leaf/group heuristics；其 `owned_*` / `composite_*` 结果进入 Policy 27 前会被清空。兼容层只提供可重新验证的 correspondence evidence，不能与 Policy 27 竞争像素 ownership。

这样避免同一 PSD Group 被两套算法分别拆分，造成 KEEP / ADD 比例漂移或重复绘制。

## 7. 当前稳定性约束

Target Visual Bundle 可以落到一个或多个**现有** FGUI visual host，但分配规则固定：

- 已有直接证据、且允许目标态换皮的 runtime/state host，只保留自己的直接 PSD leaf，例如独立 icon。
- 剩余 background / border / ornament / glow 统一由一个 primary existing host 吸收。
- 不允许为了降低 ADD 数量，把剩余 leaf 随机分给空闲 host。
- 两个 runtime/state host 抢同一个 PSD leaf 时必须 BLOCK。
- 如果只有一个安全 host，它可以承担完整 Target Visual Bundle。
- 带 GearXY / GearSize / GearLook / GearColor / GearAnimation 等会改变几何或外观状态的 host，未证明 page-level ownership 前不能直接承担整包皮肤。
- Anchor 选择使用稳定视觉规则（优先已有直接对应，否则最大可见 leaf + source order），不依赖 source-id 字典序。
- Policy revision 已参与 replacement session 的 idempotency key，升级 policy 后不会误复用旧 mapping session。

## 8. 验收门槛

### Mapping / structure

- matched semantic Component 主要表现为 `MODIFY`。
- matched PSD Group 内 decorative `ADD = 0`。
- Runtime Contract audit 通过。
- 当前状态不存在 old-skin + new-skin 双重 ownership。
- unresolved / blocked = 0 才允许 build。

### Editor

- FairyGUI Editor 6.1.4 能正常打开工程和目标组件。
- Controller / Gear / Relation / Transition 行为需要回归检查。
- 目标状态截图与 reference 对比。
- 当前正式门槛仍使用 `mean_pixel_difference == 0.0`；若字体/PSD renderer 导致无法达到，需要明确报告原因，禁止虚报完成。

## 9. 当前已知剩余风险

Policy 27 已统一 mapping 架构，但以下属于独立的渲染精度问题：

- PSD masks / clipping / blend / layer effects 的 faithful RenderSet 输出。
- Photoshop 与 `psd-tools` group/local composite 差异。
- FairyGUI 与 PSD 的字体 rasterization、baseline、stroke 差异。
- 状态型视觉如果需要修改 GearIcon/GearColor 的具体 page value，需要额外状态级 writer 证据；当前未证明时应 BLOCK。

这些问题不能通过重新引入参考图裁片或新增覆盖对象解决。

## 10. 当前质量门槛

- Ruff 必须通过。
- Policy 27 专项 regression tests 必须通过。
- Policy 27 核心模块单独执行 strict mypy。
- 仓库历史 mypy 债务使用非递增 ceiling 监控，禁止继续增长。
- Linux CI 不应把 Windows-only integration case 当成通用单测执行；对应功能需要独立 Windows gate。

## 11. 当前开发分支

- Branch: `fix/policy27-semantic-reskin`
- PR: #3
- Policy constant: `HIFI_MAPPING_POLICY_REVISION = 27`

合并前必须以真实 Tower PSD + FGUI 样例重新跑 mapping/build/Editor 验证。