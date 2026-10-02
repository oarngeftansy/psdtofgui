# Repair Notes — Current Status

当前权威策略是 **Policy 27 Semantic Reskin**。Policy 25 / 26 的 repair 记录已经失效，不再作为实现依据。

历史问题的有效结论已经合并进 `HANDOFF.md`：

- runtime identity 与 visual contribution 必须分离；
- logical bounds 与 visual bounds 必须分离；
- PSD reskin 不能依赖新增覆盖对象；
- 旧静态视觉允许 target-state retirement；
- Controller/Gear/Transition/runtime visual 未证明 ownership 时必须 BLOCK；
- Component ↔ PSD Group 是第一层语义配对；
- Legacy Visual Bundle ↔ Target Visual Bundle 是默认换皮单位；
- leaf 仅负责资源实现，不再决定业务级 KEEP / MODIFY / ADD。

## Policy 27 audit 修复

2026-10-02 的全项目一致性审计进一步处理：

1. Policy 27 semantic normalizer 在进入语义分配前清空旧 raw mapper 的 `owned_* / composite_*` ownership metadata，避免两套 group ownership 算法竞争。
2. Target Visual Bundle 使用确定性的 hybrid ownership：已证明的安全 runtime/state host 可保留自己的直接 leaf，一个 primary existing host 吸收剩余装饰视觉；禁止随机给空闲 host 分 leaf。
3. GearXY/GearSize/GearLook/GearColor/GearAnimation 等动态属性未证明 page-level ownership 时，不允许对应 host 直接承担完整视觉包。
4. Anchor 改为稳定视觉规则，不依赖 source-id 字典序。
5. 删除未进入生产路径的重复 `hifi_target_state.py` evaluator；生产 target-state truth 继续由 instance-qualified `inspect_component_tree()` 负责，包括 instance controller overrides。
6. 测试中的 policy revision 不再硬编码旧版本号，应使用 `HIFI_MAPPING_POLICY_REVISION`。
7. replacement session idempotency 已加入 policy revision，升级 policy 后不会复用旧 mapping。
8. 当前交接文档只描述 Policy 27；Policy 25/26 属于历史实现。
9. 已加入 Policy 27 CI；Ruff 和专项 regression tests 已有独立 gate，核心 Policy 27 模块增加独立 strict-mypy gate。

## 全仓 CI 审计发现的历史问题

完整 Linux unit suite 暴露出若干与 Policy 27 无关、但会影响仓库稳定性的旧问题：

- Typer/Click 行为对宽松依赖版本范围敏感，部分 CLI 错误文本测试会随依赖升级漂移；
- JSON 超深响应的错误分类存在 `response_json` / `response_schema` 不一致；
- new-project builder 的失败 gate / diagnostic 名称与测试存在漂移；
- Windows LAN 集成测试没有正确隔离平台，在 Linux runner 会直接访问 `SystemRoot` / `powershell.exe`。

这些不应伪装成 Policy 27 回归。修复原则是：固定可重复的依赖/错误语义，并把 Windows-only 行为放进 Windows gate，而不是简单删除失败测试。

## 仍需真实环境验证

GitHub CI 无法替代 FairyGUI Editor 6.1.4 的真实渲染和交互检查，因此不能仅凭单测宣称最终候选完成。

合并前仍需使用真实 Tower PSD + FGUI 输入验证：

- Noble / Merit / Challenge 顶层语义应以 MODIFY 为主；
- matched PSD Group 内 decorative ADD 应为 0；
- 当前状态不得出现 old-skin + new-skin 双重像素贡献；
- 其他 Controller 状态逻辑和视觉必须回归；
- 最终截图按项目既定 pixel-diff gate 验收。