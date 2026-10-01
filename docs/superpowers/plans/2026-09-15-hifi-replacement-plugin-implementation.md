# HIFI Replacement Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前 640×800 Figma → FairyGUI Writer 插件中增加“HIFI 替换”Tab，让用户上传旧 FGUI 工程、选择 Package/目标目录/根组件、用当前 Figma 选择完成对象映射、生成局部修改候选、审核并交付 ZIP。

**Architecture:** 保留 `ProjectWorkflowPage → NewProjectWriterPanel` 和现有 Figma selection bridge。新增独立的 HIFI replacement 领域模型、只读工程检查器、映射器、白名单补丁器、候选状态机和 API；产品 UI 通过新的 client 方法使用同一套服务。旧 FGUI 工程始终作为不可变快照，候选在副本中生成，不能调用现有 `generate_staging()` 重建整个目标组件。

**Tech Stack:** Python 3.11、Pydantic 2、lxml、Pillow、FastAPI、SQLite、React、TypeScript、Vitest、Testing Library、Playwright、FairyGUI 6.1.4。

## Implementation status — 2026-09-15

- 已实现 Task 1–12 的功能闭环：固定样例、严格领域合同、目标树、旧组件清单、结构视图、确定性映射、白名单补丁、SQLite 会话、API、插件 Tab、目标选择、双侧联动高亮、候选审核和批准后交付。实际组件拆分与计划中的建议文件名有少量差异，但职责和验收行为已覆盖。
- HIFI 图片资源会写入原 Package 下的 Img/HIFI/<根组件>/，登记到原 package.xml；UI 不把该内部派生路径作为用户选择项显示。
- 原 ZIP 保持不可变；候选由带 before-hash 的 ChangeBundle 生成。旧对象不删除，共享组件定义不修改，未知 XML 保留；已确认映射允许更新对象几何、文字和私有图片引用。
- Task 13 的自动验收已实现：使用真实旧工程 ZIP 和 Figma selection 跑通上传、目标选择、人工映射、候选构建、对象/文件差异审核、哈希绑定批准和交付；浏览器测试直接加载插件构建产物并验证 640×800 完整流程。最新测试数量见 `docs/validation/2026-09-15-hifi-replacement-acceptance.md`。
- Task 13 的 FairyGUI 6.1.4 人工打开、保存、重开仍未执行，因为当前环境没有该 Editor 可执行文件。系统要求用户对候选完成布局、引用、Controller/Gear/Transition 三项检查，未全部确认时正式 ZIP 接口返回 hifi_editor_checks_incomplete 或 hifi_download_blocked。

## Behavior preservation update — 2026-09-24

- 使用真实 `SampleLegacy` 包审计后，保护范围扩展到 Controller 页 ID、Controller Action、所有 Gear 类型、Transition 时间轴、Relation、Button 模式、嵌套组件引用、实例 Controller 参数、Button/Label/ProgressBar 参数、property/customProperty 和运行时文本。
- `FguiComponentInventory` 现在生成对象级行为角色、动态属性、实例契约、递归引用闭包与保护哈希；页 ID 原样保存，不假设连续。
- 候选审核新增视觉遮挡门禁：新增 HIFI 图层位于行为对象上方且 bounds 相交时，候选不可批准。`touchable=false` 只能保护点击穿透，不能证明状态视觉仍可见。
- PSD 只有默认画面时，只能证明默认态视觉。其他状态可保留旧外观，但必须标记 HIFI 等价未验证；完整换肤仍需状态素材和 FairyGUI Editor 的逐状态渲染证据。
- 真实包证据与后续工作记录在 `docs/research/2026-09-24-legacy-package-behavior-audit.md`。

## Global Constraints

- 正式 UI 保持当前插件 `640×800` 尺寸、`writer-shell` 页面骨架、蓝色视觉样式和固定底部操作栏。
- “新建工程”现有流程和测试必须保持通过；HIFI 替换作为并列 Tab 接入。
- 首个可写版本固定为 FairyGUI 6.1.4；其他版本只返回不支持写入的诊断。
- 用户只确认 Package、目标目录和根组件；新图片落盘路径由系统推导，不作为范围字段暴露。
- 原始上传 ZIP 永不原地修改；审核候选和最终交付包必须来自同一候选版本。
- 保持旧对象 `id/name/type`、父子次序、几何、controller、gear、transition、relation、mask 和未知 XML；只有确认计划中的字段允许变化。
- HIFI 比旧界面多出的普通视觉叶子可以在规则允许时新增；旧 FGUI 多出的对象默认保留。删除、隐藏或修改共享组件定义必须有明确证据和单独决策。
- 所有映射和批准都绑定 `project_fingerprint + selection_fingerprint + target_ref + mapping_revision`；输入变化后旧确认失效。
- 没有真实渲染证据时 UI 必须写“结构预览”，不能把拼装缩略图称为 FairyGUI 最终画面。
- API 模型继续使用 `version: 1`、`extra="forbid"`、设备所有权校验、稳定错误码和哈希验证。

---

## 1. 当前能力与缺口

| 原型效果 | 当前可复用能力 | 仍需实现 |
| --- | --- | --- |
| “新建工程 / HIFI 替换”Tab | `ProjectWorkflowPage` 已负责产品入口 | 共享 Writer 外壳、产品 Tab 和 HIFI 页面状态 |
| 当前 HIFI Frame | `selection-preflight`、`selection-export`、`SelectionUploader` | 将选择快照绑定到替换会话 |
| 上传旧工程 ZIP | `/v1/projects/uploads`、`ProjectStore`、安全解压与指纹 | 上传后保留 project ID，并读取可选目标树 |
| Package / 目录 / 根组件选择 | `index_uploaded_project()` 只返回包和文件 | 解析 `package.xml` 与组件 XML，输出稳定目标身份和可选树 |
| 双侧组件高亮 | Figma manifest 有节点层级和 bounds；已有资源缩略图接口 | 旧 FGUI 对象树、标准化 bounds、结构预览、双侧映射坐标 |
| 自动映射与人工改选 | `component_mapping.py` 只校验静态组件目录映射 | 实例级对象匹配、置信度、未匹配分类、人工决策和版本失效 |
| 局部替换 | `apply.py` 有哈希、原子写入、备份和回滚 | 保留原 XML 的字段补丁器、新资源登记、保护字段校验 |
| 候选差异审核 | 新建工程已有 review/adjust/approve/download 交互 | HIFI 专用 review 数据、审核候选下载、Editor 检查记录、最终批准 |
| 未选范围无变化 | `ChangeBundle` 和 `build_project_package()` | 目标范围策略、全工程文件差异白名单和候选闭包验证 |

## 2. 首版可交付边界

首版必须完成一条真实纵向链路：选择一个私有根组件，确认若干 1:1 图片映射，允许新增无交互的私有视觉图片节点，保留 HIFI 中缺失的旧 FGUI 对象，生成可由 FairyGUI 6.1.4 打开的候选 ZIP，展示实际文件和对象差异，记录 Editor 检查后批准下载。

以下情况首版只展示并阻断或保留，不自动修改：共享组件定义、跨包组件克隆、controller/gear/transition 控制的目标属性、动态 loader/list URL、代码或配置引用未知、交互节点新增、删除旧对象、N:1/1:N 映射、无法解析的 XML 子树。

“HIFI 多或少组件”在本计划中按界面对象处理：

- HIFI 新增：仅允许新增 `image` 或无交互 `group` 下的私有视觉叶子；其他类型列为例外。
- HIFI 缺失：旧对象默认保留并在报告中标注；首版不删除。
- 1:1 匹配：首版实际写入以静态 image 资源重定向和已验证的普通文本视觉字段为主。

## 3. 文件结构

### 新增后端文件

- `src/figma_to_fgui/hifi_replacement_models.py`：目标、对象、映射、决策、候选和审核合同。
- `src/figma_to_fgui/hifi_project_inspector.py`：FGUI 6.1.4 包、目录、组件、对象、保护字段与引用读取。
- `src/figma_to_fgui/hifi_preview.py`：旧 FGUI 结构预览和双侧标准化高亮坐标。
- `src/figma_to_fgui/hifi_mapping.py`：确定性候选排序、未匹配分类和映射决策校验。
- `src/figma_to_fgui/hifi_patch.py`：基于原 XML 的白名单字段补丁和新资源登记。
- `src/figma_to_fgui/hifi_review.py`：候选差异、保护字段、范围外变化和审核摘要。
- `src/figma_to_fgui/hifi_replacement_store.py`：替换会话、revision、候选状态和所有权持久化。
- `src/figma_to_fgui/hifi_replacement_workflow.py`：检查、映射、构建、验证、批准的编排。

### 新增前端文件

- `apps/web-console/src/figma/PluginWriterShell.tsx`：共享插件标题、产品 Tab、阶段栏、滚动区和底栏。
- `apps/web-console/src/figma/HifiReplacementPanel.tsx`：HIFI 替换状态机和 Figma 消息编排。
- `apps/web-console/src/figma/HifiTargetPicker.tsx`：Package/目录/根组件选择树。
- `apps/web-console/src/figma/HifiMappingPanel.tsx`：双侧结构预览、当前对象高亮和人工改选。
- `apps/web-console/src/figma/HifiReplacementReviewPanel.tsx`：差异审核、候选下载、Editor 检查和批准。
- `apps/web-console/src/figma/useHifiPreviewObjects.ts`：预览 Blob 加载、URL 生命周期和失败状态。

### 修改现有文件

- `src/figma_to_fgui/service_contracts.py`：导出 HIFI API 合同。
- `src/figma_to_fgui/api.py`：注册 HIFI 目标树、会话、映射、构建、审核和下载端点。
- `apps/figma-plugin/src/project-client.ts`：新增 HIFI client 类型、解析器和请求方法。
- `apps/web-console/src/figma/ProjectWorkflowPage.tsx`：产品 Tab 路由。
- `apps/web-console/src/figma/NewProjectWriterPanel.tsx`：迁入共享 Writer 外壳，行为保持不变。
- `apps/web-console/src/styles.css`：HIFI 组件样式，复用现有 writer tokens。

---

### Task 1: 建立真实 HIFI 替换固定样例

**Files:**
- Create: `tests/fixtures/hifi_replacement/old_project/`
- Create: `tests/fixtures/hifi_replacement/hifi-selection.json`
- Create: `tests/fixtures/hifi_replacement/expected-scope.json`
- Create: `tests/unit/test_hifi_replacement_fixture.py`

**Interfaces:**
- Produces: 一个 FairyGUI 6.1.4 工程，包含 `MyVillage/Panel/Panel_MyVillage_Sketchboard.xml`、共享按钮、私有图片、gear/transition 引用、一个 HIFI 新增视觉叶子和一个 HIFI 缺失旧对象。

- [ ] **Step 1: 写固定样例合同测试**

```python
def test_hifi_fixture_covers_first_release_cases() -> None:
    root = Path("tests/fixtures/hifi_replacement/old_project")
    component = etree.parse(str(root / "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"))
    ids = {node.get("id") for node in component.xpath(".//*[@id]")}
    assert {"title_bar", "silhouette_01", "btn_next", "btn_reset"} <= ids
    assert component.xpath(".//gearDisplay | .//transition")
    selection = json.loads(Path("tests/fixtures/hifi_replacement/hifi-selection.json").read_text("utf-8"))
    assert selection["display_name"] == "速记板 / HIFI v4"
    assert any(node["name"] == "ProgressBubble" for node in walk(selection["top_level_nodes"]))
```

- [ ] **Step 2: 运行测试并确认因样例缺失失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_fixture.py -q`

Expected: FAIL，指出固定工程或 selection JSON 不存在。

- [ ] **Step 3: 创建最小但完整的 6.1.4 样例**

样例必须包含合法 `project.xml`、`package.xml`、组件 XML、实际 PNG、资源 ID 闭包和上述四种风险对象。PNG 使用测试内生成的固定字节，不提交私有生产素材。

- [ ] **Step 4: 验证样例**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_fixture.py tests/unit/test_fgui_xml_dialect_614.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/hifi_replacement tests/unit/test_hifi_replacement_fixture.py
git commit -m "test: add hifi replacement fixture"
```

### Task 2: 定义替换领域合同和安全策略

**Files:**
- Create: `src/figma_to_fgui/hifi_replacement_models.py`
- Modify: `src/figma_to_fgui/service_contracts.py`
- Create: `tests/unit/test_hifi_replacement_models.py`

**Interfaces:**
- Produces: `HifiTargetRef`、`FguiObjectRef`、`HifiMappingItem`、`HifiMappingDecision`、`HifiReplacementView`、`HifiReplacementReview`。

- [ ] **Step 1: 写拒绝不安全合同的测试**

```python
def test_mapping_decision_rejects_delete_and_stale_revision() -> None:
    with pytest.raises(ValidationError):
        HifiMappingDecision(version=1, mapping_revision=2, item_id="m1", action="delete")
    decision = HifiMappingDecision(
        version=1, mapping_revision=2, item_id="m1", action="keep_old", figma_node_id=None
    )
    assert decision.action == "keep_old"
```

- [ ] **Step 2: 运行测试并确认模型缺失**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_models.py -q`

Expected: FAIL with import error。

- [ ] **Step 3: 实现严格版本化模型**

```python
class HifiTargetRef(StrictVersionedModel):
    project_id: str
    project_fingerprint: Sha256
    package_id: str
    package_name: str
    directory: str
    component_id: str
    component_name: str
    component_relative_path: str


class HifiMappingDecision(StrictVersionedModel):
    mapping_revision: int = Field(ge=1)
    item_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    action: Literal["accept", "retarget", "keep_old", "add_visual", "exception"]
    figma_node_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def require_retarget_node(self) -> Self:
        if (self.action == "retarget") != (self.figma_node_id is not None):
            raise ValueError("retarget requires exactly one figma_node_id")
        return self
```

`HifiMappingItem.action` 的可选值由服务端根据证据生成，客户端不能通过构造 JSON 开启 `delete` 或共享定义修改。

- [ ] **Step 4: 运行模型与服务合同测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_models.py tests/unit/test_service_contracts.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/hifi_replacement_models.py src/figma_to_fgui/service_contracts.py tests/unit/test_hifi_replacement_models.py
git commit -m "feat: define hifi replacement contracts"
```

### Task 3: 读取可选择的 FGUI 目标树

**Files:**
- Create: `src/figma_to_fgui/hifi_project_inspector.py`
- Create: `tests/unit/test_hifi_project_inspector.py`
- Modify: `src/figma_to_fgui/api.py`
- Modify: `tests/unit/test_project_api.py`

**Interfaces:**
- Consumes: `ProjectStore.get(project_id)`、`ProjectStore.artifact_path(project_id)`。
- Produces: `inspect_hifi_targets(root: Path, project: UploadedProjectVersion) -> HifiProjectTreeView`。
- Produces endpoint: `GET /v1/projects/{project_id}/hifi-targets`。

- [ ] **Step 1: 写目录和根组件身份测试**

```python
def test_inspector_returns_package_directory_and_component_identity() -> None:
    project = index_uploaded_project(FIXTURE, "old.zip")
    tree = inspect_hifi_targets(FIXTURE, project)
    panel = next(node for node in tree.packages[0].directories if node.path == "Panel")
    target = next(node for node in panel.components if node.name == "Panel_MyVillage_Sketchboard")
    assert target.resource_id
    assert target.relative_path == "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_project_inspector.py -q`

Expected: FAIL with missing inspector。

- [ ] **Step 3: 实现只读目标树**

解析 `package.xml` 中 `component` 资源的 `path/name/id`，按真实 path 分组为目录。只把存在且可解析的组件 XML 作为根组件候选；`Base/Common` 不按名称硬编码锁定，锁定状态来自配置或检测到的共享引用策略，并在 view 中返回 `selectable` 与 `reason`。

- [ ] **Step 4: 增加所有权受控 API**

```python
@app.get("/v1/projects/{project_id}/hifi-targets")
def get_hifi_targets(project_id: str, request: Request) -> HifiProjectTreeView:
    device_id = require_device(request)
    version = load_uploaded_project_for_device(project_id, device_id)
    return inspect_hifi_targets(project_store.artifact_path(version.project_id), version)
```

- [ ] **Step 5: 验证 API 不泄露绝对路径**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_project_inspector.py tests/unit/test_project_api.py -q`

Expected: PASS；响应仅含相对路径和公开身份。

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/hifi_project_inspector.py src/figma_to_fgui/api.py tests/unit/test_hifi_project_inspector.py tests/unit/test_project_api.py
git commit -m "feat: expose hifi replacement target tree"
```

### Task 4: 建立旧组件对象清单、保护快照和结构预览

**Files:**
- Modify: `src/figma_to_fgui/hifi_project_inspector.py`
- Create: `src/figma_to_fgui/hifi_preview.py`
- Create: `tests/unit/test_hifi_component_inventory.py`
- Create: `tests/unit/test_hifi_preview.py`

**Interfaces:**
- Produces: `inspect_component(root, target) -> FguiComponentInventory`。
- Produces: `build_structure_preview(inventory, index) -> HifiStructurePreview`。

- [ ] **Step 1: 写保护字段与引用测试**

```python
def test_inventory_keeps_identity_behavior_and_unknown_xml() -> None:
    inventory = inspect_component(FIXTURE, TARGET)
    reset = inventory.by_object_id["btn_reset"]
    assert reset.identity.name == "Btn_Reset"
    assert reset.behavior.controller_refs
    assert reset.behavior.transition_refs
    assert reset.protected_sha256 == EXPECTED_RESET_PROTECTED_SHA256
    assert inventory.parse_coverage.complete is False
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_component_inventory.py tests/unit/test_hifi_preview.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现对象清单**

每个对象至少返回稳定 ID、name、tag/type、父 ID、子序号、`xy/size`、资源引用、可修改字段、controller/gear/transition/relation/mask 引用和保护子树哈希。未知标签和未知属性进入 `parse_coverage`，不被丢弃。

- [ ] **Step 4: 实现明确标注的结构预览**

将组件尺寸归一化为 `[0,1]` bounds；已知 image 资源使用现有 thumbnail URL，其他对象用类型轮廓。输出 `evidence_kind="structure_preview"`，禁止返回 `rendered_preview`。

- [ ] **Step 5: 验证坐标与高亮**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_component_inventory.py tests/unit/test_hifi_preview.py -q`

Expected: PASS；每个可映射对象都有位于 `[0,1]` 内的高亮 bounds。

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/hifi_project_inspector.py src/figma_to_fgui/hifi_preview.py tests/unit/test_hifi_component_inventory.py tests/unit/test_hifi_preview.py
git commit -m "feat: inspect and preview existing fgui components"
```

### Task 5: 生成确定性对象映射草案

**Files:**
- Create: `src/figma_to_fgui/hifi_mapping.py`
- Create: `tests/unit/test_hifi_mapping.py`

**Interfaces:**
- Consumes: `FguiComponentInventory` 和已上传 `SelectionManifest`。
- Produces: `build_mapping(inventory, manifest) -> HifiMappingDraft`。
- Produces: `apply_mapping_decision(draft, decision) -> HifiMappingDraft`。

- [ ] **Step 1: 写多、少、歧义三类测试**

```python
def test_mapping_classifies_matched_added_missing_and_uncertain() -> None:
    draft = build_mapping(INVENTORY, HIFI_MANIFEST)
    assert draft.by_old_id["silhouette_01"].status == "matched"
    assert draft.by_figma_id["progress-bubble"].status == "hifi_added"
    assert draft.by_old_id["btn_reset"].status == "fgui_only"
    assert any(item.status == "uncertain" for item in draft.items)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_mapping.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现候选评分**

确定性评分固定由以下证据组成并逐项返回：归一化中心距离、尺寸比例、父级映射、兄弟顺序、节点类型兼容、规范化名称。分数只用于排序：唯一高分项仍标 `suggested`，低于阈值或前两名差值不足时标 `uncertain`，不得自动批准。

- [ ] **Step 4: 实现人工决策与 revision**

`accept/retarget/keep_old/add_visual/exception` 每次产生新 `mapping_revision`。`retarget` 必须指向当前 selection 中存在且未被其他 1:1 决策占用的节点；旧 revision 返回 `stale_mapping`。

- [ ] **Step 5: 验证确定性和冲突**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_mapping.py -q`

Expected: PASS；相同输入序列化结果字节一致。

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/hifi_mapping.py tests/unit/test_hifi_mapping.py
git commit -m "feat: build deterministic hifi object mappings"
```

### Task 6: 实现白名单局部补丁器

**Files:**
- Create: `src/figma_to_fgui/hifi_patch.py`
- Create: `tests/unit/test_hifi_patch.py`
- Modify: `src/figma_to_fgui/validate.py`
- Modify: `tests/unit/test_validate.py`

**Interfaces:**
- Produces: `build_hifi_change_bundle(root, inventory, selection, mapping) -> ChangeBundle`。
- Produces: `validate_hifi_candidate(before_root, after_root, target, mapping) -> tuple[Diagnostic, ...]`。

- [ ] **Step 1: 写“只改允许字段”的失败测试**

```python
def test_patch_retargets_private_image_without_rebuilding_component(tmp_path: Path) -> None:
    bundle = build_hifi_change_bundle(FIXTURE, INVENTORY, SELECTION, CONFIRMED_MAPPING)
    after = apply_to_copy(FIXTURE, bundle, tmp_path)
    before_xml = etree.parse(str(FIXTURE / TARGET.component_relative_path))
    after_xml = etree.parse(str(after / TARGET.component_relative_path))
    assert protected_snapshot(after_xml) == protected_snapshot(before_xml)
    assert image_src(after_xml, "silhouette_01") != image_src(before_xml, "silhouette_01")
    assert image_src(after_xml, "btn_reset") == image_src(before_xml, "btn_reset")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_patch.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现图片重定向和资源登记**

新资源 ID 使用已有稳定 ID 工具；路径由 `package/Img/HIFI/<root>/` 自动推导。补丁器只修改确认对象的 `src/pkg` 或已验证文本视觉属性，并在原 `package.xml` 的 `<resources>` 中插入新条目；保留原节点、注释、未知属性和未选文件字节。

- [ ] **Step 4: 实现受限新增视觉节点**

仅当 Figma 节点分类为静态 image、父映射唯一、bounds 有效、无交互/组件语义时允许 `add_visual`。新增节点使用新对象 ID并附着到确认父节点；旧对象删除动作不存在。

- [ ] **Step 5: 增加候选不变量校验**

校验项目包括：目标外 XML 哈希不变、目标组件保护快照不变、所有 `src/pkg` 闭包有效、新资源字节/尺寸/后缀一致、package ID/resource ID 无冲突、映射全覆盖、未知解析区域未变化。

- [ ] **Step 6: 运行补丁与回滚测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_patch.py tests/unit/test_validate.py tests/unit/test_apply.py tests/unit/test_project_package.py -q`

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/figma_to_fgui/hifi_patch.py src/figma_to_fgui/validate.py tests/unit/test_hifi_patch.py tests/unit/test_validate.py
git commit -m "feat: patch hifi visuals without rebuilding fgui components"
```

### Task 7: 建立替换会话、候选和批准状态机

**Files:**
- Create: `src/figma_to_fgui/hifi_replacement_store.py`
- Create: `src/figma_to_fgui/hifi_replacement_workflow.py`
- Create: `tests/unit/test_hifi_replacement_store.py`
- Create: `tests/unit/test_hifi_replacement_workflow.py`

**Interfaces:**
- Produces states: `inspecting → mapping → building → review_ready → approved → delivered`，以及 `failed/rejected/superseded`。
- Consumes: ProjectStore、SelectionStore、Task 3–6 的检查/映射/补丁/验证函数。

- [ ] **Step 1: 写状态迁移和失效测试**

```python
def test_mapping_change_supersedes_review_candidate(store: HifiReplacementStore) -> None:
    session = store.begin(OWNER, PROJECT, SELECTION, TARGET)
    review_ready = store.complete_build(session.id, session.mapping_revision, ARTIFACT)
    changed = store.save_decision(session.id, OWNER, DECISION)
    assert changed.mapping_revision == review_ready.mapping_revision + 1
    assert changed.status == "mapping"
    with pytest.raises(StaleCandidate):
        store.approve(session.id, OWNER, review_ready.candidate_generation, EDITOR_CHECKS)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_store.py tests/unit/test_hifi_replacement_workflow.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现 SQLite 存储和文件布局**

每个会话保存 owner device、输入指纹、target、mapping JSON 哈希、revision、candidate generation、状态和诊断。候选目录只保存服务端路径；API view 不返回绝对路径。写操作使用事务和期望 revision。

- [ ] **Step 4: 实现工作流编排**

构建流程固定为：重新校验输入指纹 → 生成 ChangeBundle → 复制工程并应用 → 运行 HIFI 不变量校验 → 调用 `build_project_package(..., mode="update")` → 生成 review → 原子发布候选元数据。

- [ ] **Step 5: 运行并发、重试和恢复测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_store.py tests/unit/test_hifi_replacement_workflow.py -q`

Expected: PASS；重复 idempotency key 返回同一会话，过期 build 可恢复或失败，不会产生两个当前候选。

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/hifi_replacement_store.py src/figma_to_fgui/hifi_replacement_workflow.py tests/unit/test_hifi_replacement_store.py tests/unit/test_hifi_replacement_workflow.py
git commit -m "feat: add hifi replacement candidate lifecycle"
```

### Task 8: 提供 HIFI Replacement API

**Files:**
- Modify: `src/figma_to_fgui/api.py`
- Create: `tests/unit/test_hifi_replacement_api.py`

**Interfaces:**
- Produces endpoints:
  - `POST /v1/hifi-replacements`
  - `GET /v1/hifi-replacements/{replacement_id}`
  - `GET /v1/hifi-replacements/{replacement_id}/mapping`
  - `POST /v1/hifi-replacements/{replacement_id}/mapping-decisions`
  - `POST /v1/hifi-replacements/{replacement_id}/build`
  - `GET /v1/hifi-replacements/{replacement_id}/review`
  - `GET /v1/hifi-replacements/{replacement_id}/candidate/download`
  - `POST /v1/hifi-replacements/{replacement_id}/approve`
  - `POST /v1/hifi-replacements/{replacement_id}/reject`
  - `GET /v1/hifi-replacements/{replacement_id}/download`

- [ ] **Step 1: 写完整 API 生命周期测试**

```python
def test_hifi_api_requires_mapping_then_review_then_approval(client: TestClient) -> None:
    replacement = create_replacement(client, PROJECT_ID, SELECTION_ID, TARGET)
    mapping = client.get(f"/v1/hifi-replacements/{replacement['replacement_id']}/mapping").json()
    confirm_every_item(client, replacement, mapping)
    built = client.post(f"/v1/hifi-replacements/{replacement['replacement_id']}/build", json={"version": 1, "mapping_revision": mapping["mapping_revision"]})
    assert wait_until_review_ready(client, built.json())["status"] == "review_ready"
    assert client.get(f"/v1/hifi-replacements/{replacement['replacement_id']}/download").status_code == 409
```

- [ ] **Step 2: 运行测试并确认 404**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_api.py -q`

Expected: FAIL with endpoint not found。

- [ ] **Step 3: 实现端点与稳定错误码**

错误码至少包括 `hifi_target_stale`、`hifi_mapping_incomplete`、`hifi_mapping_stale`、`hifi_build_failed`、`hifi_candidate_stale`、`hifi_editor_checks_incomplete`、`hifi_download_blocked`。候选下载允许 `review_ready`；正式下载只允许当前 `approved` generation。

- [ ] **Step 4: 验证设备隔离、并发和下载哈希**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_replacement_api.py tests/unit/test_api.py tests/unit/test_project_api.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/figma_to_fgui/api.py tests/unit/test_hifi_replacement_api.py
git commit -m "feat: expose hifi replacement workflow api"
```

### Task 9: 扩展插件 client 和产品 Tab 外壳

**Files:**
- Modify: `apps/figma-plugin/src/project-client.ts`
- Modify: `apps/figma-plugin/src/project-client.test.ts`
- Create: `apps/web-console/src/figma/PluginWriterShell.tsx`
- Create: `apps/web-console/src/figma/PluginWriterShell.test.tsx`
- Modify: `apps/web-console/src/figma/ProjectWorkflowPage.tsx`
- Modify: `apps/web-console/src/figma/ProjectWorkflowPage.test.tsx`
- Modify: `apps/web-console/src/figma/NewProjectWriterPanel.tsx`

**Interfaces:**
- Produces: `ProjectWorkflowClient` 的 `uploadHifiProject`、`hifiTargets`、`createHifiReplacement`、`hifiMapping`、`decideHifiMapping`、`buildHifiCandidate`、`hifiReview`、`downloadHifiCandidate`、`approveHifiReplacement`、`downloadHifiReplacement`。
- Produces: `PluginWriterShellProps`，接收 `activeProduct`、`title`、`meta`、`rail`、`footer`、`children`。

- [ ] **Step 1: 写严格响应解析和 Tab 回归测试**

```tsx
it("keeps new project and opens hifi replacement as a sibling tab", async () => {
  render(<ProjectWorkflowPage client={client} defaultMode="writer" />);
  expect(screen.getByRole("tab", { name: "新建工程" })).toHaveAttribute("aria-selected", "true");
  await userEvent.click(screen.getByRole("tab", { name: "HIFI 替换" }));
  expect(screen.getByRole("heading", { name: "HIFI 替换" })).toBeVisible();
  await userEvent.click(screen.getByRole("tab", { name: "新建工程" }));
  expect(screen.getByLabelText("工程名称")).toBeVisible();
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pnpm --dir apps/web-console test -- --run src/figma/PluginWriterShell.test.tsx src/figma/ProjectWorkflowPage.test.tsx && pnpm --dir apps/figma-plugin test -- project-client.test.ts`

Expected: FAIL with missing Tab/client methods。

- [ ] **Step 3: 实现 client 解析器和请求方法**

每个解析器验证 `version`、32 位公开 ID、revision/generation、状态、相对路径、枚举和数组上限。下载继续校验 Content-Type、Content-Disposition、byte size 和 SHA-256。

- [ ] **Step 4: 抽取共享 Writer 外壳并接入 Tab**

`ProjectWorkflowPage` 持有 `product: "new" | "hifi" | "legacy-update"`。新建工程和旧版更新行为保持；HIFI Tab 渲染新面板。任何运行中的流程切换 Tab 时先取消本面板 AbortController，不复用未完成请求。

- [ ] **Step 5: 运行前端与插件回归**

Run: `pnpm --dir apps/web-console test -- --run && pnpm --dir apps/figma-plugin test && pnpm --dir apps/web-console build`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add apps/figma-plugin/src/project-client.ts apps/figma-plugin/src/project-client.test.ts apps/web-console/src/figma/PluginWriterShell.tsx apps/web-console/src/figma/PluginWriterShell.test.tsx apps/web-console/src/figma/ProjectWorkflowPage.tsx apps/web-console/src/figma/ProjectWorkflowPage.test.tsx apps/web-console/src/figma/NewProjectWriterPanel.tsx
git commit -m "feat: add hifi replacement product tab"
```

### Task 10: 实现准备材料和目标选择页面

**Files:**
- Create: `apps/web-console/src/figma/HifiReplacementPanel.tsx`
- Create: `apps/web-console/src/figma/HifiReplacementPanel.test.tsx`
- Create: `apps/web-console/src/figma/HifiTargetPicker.tsx`
- Create: `apps/web-console/src/figma/HifiTargetPicker.test.tsx`
- Modify: `apps/web-console/src/styles.css`

**Interfaces:**
- Consumes: current `selection-preflight`/`selection-export` messages和 Task 9 client。
- Produces UI state: `prepare | inspecting | mapping | building | review | approved | failed`。

- [ ] **Step 1: 写目标选择行为测试**

```tsx
it("clears an incompatible root when the target directory changes", async () => {
  render(<HifiTargetPicker tree={TREE} value={PANEL_TARGET} onChange={onChange} />);
  await userEvent.click(screen.getByRole("button", { name: /Component.*17 个组件/ }));
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ directory: "Component", componentId: null }));
  expect(screen.getByRole("button", { name: "开始盘点与映射" })).toBeDisabled();
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pnpm --dir apps/web-console test -- --run src/figma/HifiTargetPicker.test.tsx src/figma/HifiReplacementPanel.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现准备材料页面**

页面依次显示当前 Figma 选择、旧工程 ZIP、真实 FGUI 目标树。Package/目录/根组件分别单选；不可选项显示服务端 reason；底部摘要只显示目标目录和根组件。缺少任一输入时主按钮禁用。

- [ ] **Step 4: 实现上传、刷新和取消**

ZIP 变化后旧 project ID、target、mapping 和 candidate 全部失效。Figma selection 变化后展示“选择已变化”，要求重新盘点。上传或盘点期间锁定输入并提供取消。

- [ ] **Step 5: 验证页面和无障碍语义**

Run: `pnpm --dir apps/web-console test -- --run src/figma/HifiTargetPicker.test.tsx src/figma/HifiReplacementPanel.test.tsx`

Expected: PASS；树使用 button/radio 语义，键盘可完成选择。

- [ ] **Step 6: Commit**

```bash
git add apps/web-console/src/figma/HifiReplacementPanel.tsx apps/web-console/src/figma/HifiReplacementPanel.test.tsx apps/web-console/src/figma/HifiTargetPicker.tsx apps/web-console/src/figma/HifiTargetPicker.test.tsx apps/web-console/src/styles.css
git commit -m "feat: add hifi replacement setup flow"
```

### Task 11: 实现组件对齐工作台

**Files:**
- Create: `apps/web-console/src/figma/HifiMappingPanel.tsx`
- Create: `apps/web-console/src/figma/HifiMappingPanel.test.tsx`
- Create: `apps/web-console/src/figma/useHifiPreviewObjects.ts`
- Create: `apps/web-console/src/figma/useHifiPreviewObjects.test.ts`
- Modify: `apps/web-console/src/styles.css`

**Interfaces:**
- Consumes: `HifiMappingDraft`、旧结构预览 URL、Figma selection preview/resource URL、映射决策 client。
- Produces: 当前项导航、双侧高亮、人工改选和全部处置完成状态。

- [ ] **Step 1: 写双侧高亮与 stale mapping 测试**

```tsx
it("moves both highlights when the current mapping changes", async () => {
  render(<HifiMappingPanel draft={DRAFT} client={client} />);
  await userEvent.click(screen.getByRole("button", { name: "下一项" }));
  expect(screen.getByTestId("fgui-focus")).toHaveAccessibleName("FGUI · color_group");
  expect(screen.getByTestId("hifi-focus")).toHaveAccessibleName("HIFI · Color_Options");
  expect(screen.getByText("19 / 31")).toBeVisible();
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pnpm --dir apps/web-console test -- --run src/figma/HifiMappingPanel.test.tsx src/figma/useHifiPreviewObjects.test.ts`

Expected: FAIL。

- [ ] **Step 3: 实现双侧预览与当前项导航**

两侧使用各自真实归一化 bounds；FGUI 侧明确标“结构预览”，HIFI 侧标来源。高亮包含对象名和同一映射序号。预览加载失败时显示文字状态，并阻止把该证据标为已看过。

- [ ] **Step 4: 实现映射动作**

支持“接受建议、重新指定 HIFI 图层、保留旧对象、允许新增视觉节点、列为例外”。“定位到 Figma 图层”继续发送现有 `locate-node` 消息。服务端返回 stale revision 时重新加载草案并保留用户当前项位置。

- [ ] **Step 5: 验证所有分类和输入失效**

Run: `pnpm --dir apps/web-console test -- --run src/figma/HifiMappingPanel.test.tsx src/figma/HifiReplacementPanel.test.tsx`

Expected: PASS；`matched/hifi_added/fgui_only/uncertain/blocked` 均有明确操作，未决项存在时不能构建。

- [ ] **Step 6: Commit**

```bash
git add apps/web-console/src/figma/HifiMappingPanel.tsx apps/web-console/src/figma/HifiMappingPanel.test.tsx apps/web-console/src/figma/useHifiPreviewObjects.ts apps/web-console/src/figma/useHifiPreviewObjects.test.ts apps/web-console/src/styles.css
git commit -m "feat: add interactive hifi mapping workbench"
```

### Task 12: 实现候选审核、Editor 检查和交付

**Files:**
- Create: `src/figma_to_fgui/hifi_review.py`
- Create: `tests/unit/test_hifi_review.py`
- Create: `apps/web-console/src/figma/HifiReplacementReviewPanel.tsx`
- Create: `apps/web-console/src/figma/HifiReplacementReviewPanel.test.tsx`
- Modify: `apps/web-console/src/figma/HifiReplacementPanel.tsx`
- Modify: `apps/web-console/src/styles.css`

**Interfaces:**
- Produces review groups: `changed | added | kept | exception`。
- Produces required Editor checks: `opens_and_saves | states_and_text | accepted_exceptions`。

- [ ] **Step 1: 写实际差异和批准门禁测试**

```python
def test_review_reports_actual_changes_and_blocks_incomplete_editor_checks() -> None:
    review = build_hifi_review(BEFORE, AFTER, TARGET, CONFIRMED_MAPPING)
    assert review.out_of_scope_changes == ()
    assert {item.kind for item in review.items} >= {"changed", "added", "kept"}
    assert review.approvable
    with pytest.raises(EditorChecksIncomplete):
        approve_review(review, ("opens_and_saves",))
```

- [ ] **Step 2: 运行后端和前端测试并确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_review.py -q && pnpm --dir apps/web-console test -- --run src/figma/HifiReplacementReviewPanel.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现 review 构建器**

review 从实际候选副本和 ChangeBundle 重新计算，不复述计划。每项包含 before/after 资源、对象身份、字段差异、影响范围、证据种类和处置；任何范围外变化或保护字段变化令 `approvable=false`。

- [ ] **Step 4: 实现审核 UI**

沿用现有 `NewProjectReviewPanel` 的逐项模式、Blob URL 清理、预览失败阻断、`locate-node` 和 `create-review-area`。候选 ZIP 在 `review_ready` 即可下载；正式交付按钮只有在服务端 review 可批准且三项 Editor 检查完成后启用。

- [ ] **Step 5: 验证批准与下载版本一致**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_review.py tests/unit/test_hifi_replacement_api.py -q && pnpm --dir apps/web-console test -- --run src/figma/HifiReplacementReviewPanel.test.tsx src/figma/HifiReplacementPanel.test.tsx`

Expected: PASS；批准后下载的 SHA-256 与审核候选记录相同。

- [ ] **Step 6: Commit**

```bash
git add src/figma_to_fgui/hifi_review.py tests/unit/test_hifi_review.py apps/web-console/src/figma/HifiReplacementReviewPanel.tsx apps/web-console/src/figma/HifiReplacementReviewPanel.test.tsx apps/web-console/src/figma/HifiReplacementPanel.tsx apps/web-console/src/styles.css
git commit -m "feat: review and deliver hifi replacement candidates"
```

### Task 13: 端到端验收和真实 FairyGUI Editor 门禁

**Files:**
- Create: `tests/integration/test_hifi_replacement_delivery.py`
- Create: `apps/web-console/e2e/hifi-replacement.spec.ts`
- Create: `scripts/run_hifi_replacement_acceptance.py`
- Create: `docs/validation/2026-09-15-hifi-replacement-acceptance.md`
- Modify: `docs/acceptance/figma-plugin-checklist.md`

**Interfaces:**
- Consumes: Task 1 固定样例和完整插件/API 流程。
- Produces: 机器结果 JSON、候选 ZIP 哈希、最终 ZIP 哈希、浏览器截图和人工 Editor 记录。

- [ ] **Step 1: 写端到端失败测试**

```python
def test_hifi_replacement_delivery_preserves_scope_and_candidate_hash(tmp_path: Path) -> None:
    result = run_hifi_replacement_fixture(tmp_path)
    assert result.mapping_counts == {"matched": 3, "hifi_added": 1, "fgui_only": 1}
    assert result.out_of_scope_changes == ()
    assert result.candidate_sha256 == result.delivered_sha256
    assert result.protected_identity_unchanged
```

- [ ] **Step 2: 运行测试并确认执行器缺失**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_hifi_replacement_delivery.py -q`

Expected: FAIL。

- [ ] **Step 3: 实现浏览器纵向流程**

Playwright 测试覆盖：切换 HIFI Tab、刷新选择、上传 ZIP、切换目录导致根组件失效、选择根组件、进入映射、双侧高亮、解决未决项、生成候选、逐项审核、下载候选、完成 Editor 检查、批准和下载交付包。

- [ ] **Step 4: 运行自动化全套检查**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/unit/test_hifi_*.py tests/integration/test_hifi_replacement_delivery.py -q
pnpm --dir apps/web-console test -- --run
pnpm --dir apps/web-console build
pnpm --dir apps/figma-plugin test
pnpm --dir apps/figma-plugin typecheck
pnpm --dir apps/web-console exec playwright test e2e/hifi-replacement.spec.ts
```

Expected: 全部 PASS。

- [ ] **Step 5: 执行 FairyGUI 6.1.4 人工验收**

使用固定候选完成：打开 → 检查目标组件普通/选中/禁用状态 → 保存 → 关闭 → 重开 → 再保存。记录 Editor 版本、候选 SHA-256、观察到的弹窗和结论。没有运行时工程时，将运行时状态记录为“未验证”，不得写“行为完全保持”。

- [ ] **Step 6: 生成验收报告并检查原型覆盖**

报告必须逐项对应原型：产品 Tab、材料准备、可选目标树、双侧高亮、人工映射、差异审核、审核候选、Editor 检查、批准交付。报告单列首版例外，不把 `fgui_only` 保留项计为替换成功。

- [ ] **Step 7: Commit**

```bash
git add tests/integration/test_hifi_replacement_delivery.py apps/web-console/e2e/hifi-replacement.spec.ts scripts/run_hifi_replacement_acceptance.py docs/validation/2026-09-15-hifi-replacement-acceptance.md docs/acceptance/figma-plugin-checklist.md
git commit -m "test: validate hifi replacement delivery flow"
```

## 4. 建议实施顺序与里程碑

| 里程碑 | Tasks | 可演示结果 | 不应提前宣称 |
| --- | --- | --- | --- |
| M1 只读盘点 | 1–4 | 上传 ZIP、真实目标树、根组件对象清单和结构预览 | 不能生成候选 |
| M2 可确认映射 | 5、8、9–11 的映射部分 | 插件内双侧高亮、自动建议、人工改选、多/少对象分类 | 不能声称局部修改安全 |
| M3 安全候选 | 6–8、12 的 review 构建 | 生成独立候选 ZIP，实际差异和保护字段检查通过 | 未经 Editor 检查不能交付 |
| M4 交付闭环 | 12–13 | 候选下载、Editor 检查记录、批准、同版本交付 | 没有运行时证据不能声称运行行为已验证 |

## 5. 验收标准

1. 新建工程流程的现有单元、集成和插件构建测试全部保持通过。
2. 用户可在同一插件窗口中切换 HIFI Tab，上传旧工程并从真实工程数据选择 Package、目录和根组件。
3. 当前 Figma 选择变化、旧 ZIP 变化或目标变化都会使旧 mapping/candidate 失效。
4. 每个旧对象和 HIFI 节点都有 `matched/hifi_added/fgui_only/uncertain/blocked` 之一，所有未决项在构建前被处理。
5. 对齐页当前项在两侧同时高亮；坐标来自真实数据，证据类型明确。
6. 候选只修改确认范围和必要的 package 资源登记；目标外文件哈希不变。
7. 旧对象身份与行为保护快照保持；共享定义、受状态控制字段和未知引用不能被静默修改。
8. 候选 ZIP 能由 FairyGUI 6.1.4 打开、保存、重开；人工结果绑定候选哈希。
9. 批准后的正式 ZIP 与审核候选工程内容哈希一致；旧候选和跨设备下载被拒绝。
10. UI 不显示自动推导的新图片路径作为用户输入，也不把结构预览描述成最终渲染。

## 6. 计划自检结论

- 原型中的三个阶段均有对应任务：准备材料为 Tasks 3、9、10；组件对齐为 Tasks 4、5、11；审核交付为 Tasks 6–8、12、13。
- HIFI 多/少对象、双侧高亮、目录选择、候选与交付分离都有明确数据合同和门禁。
- 现有插件能力有具体复用点，未把当前 `runUpdate()` 或 `generate_staging()`误写成局部替换能力。
- 首版不支持的删除、共享定义修改和动态引用均有明确处置，不存在隐藏的自动降级。
