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
2. 一个 matched semantic bundle 默认只使用一个现有 raster-capable visual host 承担完整 Target Visual Bundle；不再按 decorative leaf 扩散成多个业务 owner。
3. Anchor 改为稳定视觉规则，不依赖 source-id 字典序。
4. 删除未进入生产路径的重复 `hifi_target_state.py` evaluator；生产 target-state truth 继续由 instance-qualified `inspect_component_tree()` 负责，包括 instance controller overrides。
5. 测试中的 policy revision 不再硬编码旧版本号，应使用 `HIFI_MAPPING_POLICY_REVISION`。
6. 当前交接文档只描述 Policy 27；Policy 25/26 属于历史实现。

## 仍需真实环境验证

当前仓库没有可用的 GitHub Actions workflow，也没有在本会话环境运行 FairyGUI Editor 6.1.4，因此不能宣称最终候选已通过真实 Editor 验收。

合并前仍需使用真实 Tower PSD + FGUI 输入验证：

- Noble / Merit / Challenge 顶层语义应以 MODIFY 为主；
- matched PSD Group 内 decorative ADD 应为 0；
- 当前状态不得出现 old-skin + new-skin 双重像素贡献；
- 其他 Controller 状态逻辑和视觉必须回归；
- 最终截图按项目既定 pixel-diff gate 验收。