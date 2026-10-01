# 新建工程模式的通用 FairyGUI XML Writer 设计

日期：2026-08-18

## 1. 目标与边界

本阶段把已经验证且 `bindable=true` 的 FairyGUI Generation Plan 确定性地生成完整 FairyGUI 6.1.4 工程 ZIP。ZIP 解压后必须可由 FairyGUI Editor 6.1.4 直接打开。

主流程为：

```text
Validated FGUI Plan + NewProjectConfig + AssetPayloadSet
  → NewProjectManifest
  → Manifest 严格验证
  → FairyGUI 6.1.4 XML 与资源序列化
  → 工程结构和引用复验
  → 确定性 ZIP
  → ZIP 解包复验
  → 原子发布
```

第一版固定为一个新工程、一个生成包。每个 Plan root 生成一个顶层组件；共享资源和自包含通用组件定义位于同一包。

本阶段明确不做：

- 扫描、上传或修改已有 FairyGUI 工程。
- Project Binding、已有资源复用、用户文件合并或增量覆盖。
- 把目标 ID 回写 UIR 或 Plan。
- Controller、Gear、List、复杂 Auto Layout 或未被 Plan 批准的能力推断。
- 针对“村庄升阶”的名称、节点 ID、尺寸或结构特判；它仅是最终回归样例。

## 2. 架构选择

采用“两阶段 Writer”：先把源契约编译成纯内存 `NewProjectManifest`，再机械序列化 XML 和资源。

`NewProjectManifest` 是单次新工程构建的目标清单，不是 Project Binding。它不读取任何既有工程，不匹配既有 ID，不持久化跨工程映射。这样可在触碰文件系统前完成 ID、路径、所有权和引用闭包验证，并把 FairyGUI 方言编码与上游语义判断隔离。

不采用 Plan 直写 XML，因为这会把 ID 分配、引用解析和 XML 拼装耦合。也不以复制空白模板工程作为核心机制；真实编辑器 fixture 只用于证明 6.1.4 方言，不得携带随机状态或成为隐式运行时依赖。

## 3. 输入契约

### 3.1 Validated FGUI Plan

Writer 必须重新运行 Plan schema 和语义验证，并要求：

- `bindable=true`。
- 没有阻断诊断。
- 所有节点、资源、遮罩和组件定义引用闭合。
- Profile、规则和 schema 版本均在 Writer 明确支持的集合内。

Writer 不重新读取 Figma，不调用模型，不重新进行能力判断，也不把未知扩展当作可忽略字段。

### 3.2 NewProjectConfig

第一版配置包含：

- `projectName`
- `packageName`
- `fairyGuiVersion`，只接受 `6.1.4`
- `publishTarget`，只接受 `unity`
- 可选的公开命名策略版本

配置不得接受已有工程路径、package/component/resource ID、组件库扫描结果或资源复用映射。工程名和包名不静默改写；非法字符、保留字、Unicode 规范化冲突或路径风险直接阻断。

### 3.3 AssetPayloadSet

Plan 只携带资源身份、内容哈希和导出事实，不携带原始字节或本地路径。资源字节通过独立的 `AssetPayloadSet` 提供：

- 以 Plan `resourceId` 为唯一键。
- 每项只携带字节和声明 MIME。
- Writer 重新计算 SHA-256，识别真实格式和尺寸。
- Writer 对照 Plan 校验 MIME、格式、尺寸、九宫格和消费者。
- 缺失、多余、重复、伪装格式、损坏图片或哈希不符全部阻断。

载荷字节不得写入 Manifest 规范 JSON、日志或公共诊断。

## 4. 自包含组件定义

新建工程不能仅凭 `componentReference.candidateKey` 生成真实组件。该能力以 FGUI Plan schema v2 引入 `componentDefinitions`，不向严格的 v1 文档偷偷增加字段：

- 按稳定逻辑定义 ID 存储完整、可生成的组件节点树。
- `componentReference` 引用定义 ID，并携带已具体化的变体值。
- 定义节点使用与 root 相同的类型化 Plan、能力规则和资源规则。
- 未使用定义、悬空引用、递归引用、重复所有权和无法具体化的变体阻断。
- Writer 为定义生成本包内组件及确定性目标 ID。

Plan v1 若不含 `componentReference`，可通过显式、可验证的纯结构迁移进入 v2；包含逻辑组件引用的 v1 必须从 UIR 重新编译，以产生自包含定义或执行安全 fallback。Writer 不负责隐式迁移，也不把 candidate key 当作定义。

第一版不把变体值猜成 Controller。Controller、Gear 或运行时状态仍由上游能力门阻断；只有已经具体化为静态可生成树的组件定义可以进入 Writer。

### 4.1 需求方通用组件映射的地位

`docs/templates/component-mapping-v1.json` 是需求收集与映射评审格式，当前条目均为 `candidate`。它描述 `Figma → UIR` 语义候选以及期望的 FairyGUI 名称、路径和 Controller 页面，但不包含组件 XML、资源或节点树，因此：

- 它可作为上游语义识别、需求追踪和未来 Project Binding 的候选输入。
- 它不是 Writer 输入，不能被当作已验证生产映射。
- 其中 FairyGUI 包名、组件名和路径不是现工程真实 ID，也不能证明组件存在。
- 新建工程模式下，没有自包含定义的候选组件按声明的 `rasterSubtree` 策略安全降级；无法完整栅格化则阻断。
- 映射中的 Controller 信息保留为后续能力需求，Writer 不猜测实现。

## 5. NewProjectManifest

Manifest 完整描述：

- 工程与包元数据。
- root 组件和生成组件定义。
- 组件内对象及其稳定目标 ID。
- 图片、Loader 和 raster fallback 资源。
- 规范相对路径。
- 类型化引用关系、消费者和文件所有权。
- Writer、命名规则和 FairyGUI 方言版本。

所有目标 ID 只存在于 Manifest 和最终工程。相同 Plan 规范字节、配置、资源内容和 Writer 版本必须生成字节级相同的 Manifest。

## 6. 稳定 ID、命名与路径

ID 由带类型命名空间的规范逻辑键派生：

```text
package:<documentId>:<packageName>
component:<root-or-definition-id>
resource:<resourceId>:<contentSha256>:<exportParamsSha256>
object:<component-id>:<planNodeId>
```

使用 SHA-256 派生符合 FairyGUI 6.1.4 方言约束的固定长度小写十六进制 ID。分配前按规范逻辑键排序。截断碰撞使用完整摘要的确定性扩展规则处理；无法满足目标格式时阻断。禁止随机数、时间戳或依赖字典遍历顺序。

组件和资源文件名由可读逻辑名与稳定短摘要组成。文件名不是引用身份。大小写折叠冲突、Unicode 规范化冲突、保留字、绝对路径、路径穿越、控制字符和平台非法名称均在写入前阻断。

第一版目录固定为：

```text
<ProjectName>/
  <ProjectName>.fairy
  assets/
    <PackageName>/
      package.xml
      components/
        <root components>.xml
        <generated definitions>.xml
      resources/
        <images and raster fallbacks>
```

实现前必须用 FairyGUI Editor 6.1.4 创建的最小工程 fixture 锁定 `.fairy` 文件、目录和 XML 方言的精确格式；若编辑器要求不同的目录细节，更新的是方言常量与本规格中的固定布局，不改变 Manifest 边界。

## 7. Plan 到 XML 的映射

Writer 只执行以下机械映射：

| Plan 类型 | FairyGUI 产物 |
|---|---|
| `container` | 当前组件 display list 中的容器对象 |
| `text` | 原生文本对象 |
| `richText` | Plan 已批准的可表达富文本子集 |
| `image` | 包图片资源和图片对象 |
| `loader` | 引用本包资源的 Loader 对象 |
| `componentReference` | 引用本工程内生成的组件定义 |
| `rasterSubtree` | PNG 包资源和单个图片对象 |

规则如下：

- 每个 Plan root 和组件定义各生成一个组件 XML。
- 坐标直接采用 Plan 的父级局部坐标，不重算 Figma 布局。
- 子节点顺序严格采用 Plan `children`，并复验与 `zIndex` 一致。
- 可见性、透明度、尺寸、旋转、文本、字体、颜色、描边和对齐只来自类型化 Plan 字段。
- Writer 不猜字体替代；方言无法表达已批准声明时阻断。
- 九宫格写入资源声明，并用真实资源尺寸再次校验。
- 原生裁剪和图片遮罩由独立的 6.1.4 方言编码器输出；角色、范围和显示顺序必须与 `MaskPlan` 一致。
- 栅格子树只生成一个图片对象，内部节点不得重复输出。
- XML 使用库 API 构建和转义，禁止字符串拼接标签。
- 浮点数采用规范十进制表示，拒绝非有限值，不再次改变已验证几何。
- 未知类型、未知字段或尚未实现的合法扩展一律 fail closed。

## 8. 确定性序列化

- 通用组件定义先于 root 组件；两组内部按目标 ID 排序。
- 包资源条目、对象属性和诊断使用固定次序。
- XML 声明、UTF-8 编码、缩进、换行和空元素风格固定。
- ZIP 内路径统一为 `/` 并按规范字节序排序。
- ZIP 时间戳、权限位、压缩算法和压缩级别固定。
- 资源字节原样写入且重新核验哈希。

同一输入连续构建的 Manifest、所有 XML、文件清单与最终 ZIP 必须字节级一致。

## 9. 五道验证门与原子发布

1. **输入门**：验证 Plan、配置和全部资源载荷。
2. **Manifest 门**：验证 ID/路径唯一性、引用闭包、无环组件图、消费者和唯一所有权。
3. **XML 门**：内存 XML 重新解析并通过 6.1.4 方言结构验证器。
4. **工程门**：写入同文件系统临时目录，验证 `.fairy`、包、组件、资源和文件闭包；拒绝符号链接、绝对路径、越界路径及未声明文件。
5. **ZIP 门**：生成确定性 ZIP 后重新打开，校验 CRC、清单、哈希，并执行解包结构复验。

只有全部通过才用原子替换发布。任一道门失败都不得提供成功 ZIP，也不得触碰既有工程。临时目录在失败后清理。

## 10. 诊断

错误码按阶段分组：

```text
fgui.writer.input.*
fgui.writer.asset.*
fgui.writer.manifest.*
fgui.writer.reference.*
fgui.writer.xml.*
fgui.writer.project.*
fgui.writer.archive.*
```

每条公共诊断包含稳定错误码、阶段、逻辑对象引用、公开证据和建议动作。诊断不得暴露资源字节、绝对路径、秘密、内部堆栈或无关目标 ID。同一错误输入产生相同且有序的诊断。结构仍可信时可收集多个独立错误；结构不可信时停止后续阶段，避免级联噪声。

Writer API 只返回完整成功产物或完整失败结果，不存在部分成功。未知内部异常转换为安全的 `fgui.writer.internal_error`，受控异常链仅供本地开发。

## 11. 测试矩阵

- 严格模型：配置、组件定义、资源载荷描述和 Manifest。
- ID/命名：同名、Unicode、大小写折叠、保留字、截断碰撞、路径穿越和顺序扰动。
- 类型映射：容器、文本、富文本、图片、Loader、自包含组件引用、栅格子树、九宫格及三类已支持遮罩。
- 引用：悬空资源、递归组件、重复所有权、错误消费者及栅格子树重复输出。
- 资源：缺失、多余、重复、哈希不符、MIME 伪装、损坏图片和尺寸不符。
- 安全：ZIP Slip、符号链接、绝对路径、XML 特殊字符、实体注入及超限输入。
- 故障注入：各阶段写入、磁盘、ZIP 和发布前故障不留下成功产物。
- 确定性：相同输入及不同字典插入顺序得到字节级相同产物。
- 方言 golden：用 FairyGUI Editor 6.1.4 创建的最小真实工程锁定格式，不复制随机 ID。
- CLI 集成：从 Plan、配置和资源载荷生成 ZIP，再解包复验。
- 编辑器验收：6.1.4 直接打开，无修复提示、丢失引用或自动迁移，并记录保存前后结构差异。
- 通用 fixtures 全部通过后才运行“村庄升阶”回归和截图差异；扫描代码与规则，证明不存在样例特判。

## 12. 完成标准

1. 支持 FGUI Plan v2 中全部已实现且可自包含生成的对象类型；无组件引用的 v1 可显式迁移。
2. 自包含组件引用能在新工程内生成并解析。
3. 资源内容和引用经过完整闭环验证。
4. 输出完整工程 ZIP，FairyGUI Editor 6.1.4 可直接打开。
5. 连续生成的 ZIP 字节级相同。
6. 任何失真风险或验证失败都不发布产物。
7. “村庄升阶”只作为最终回归证据。

## 13. 后续阶段

完成并验收新建工程 Writer 后，才独立设计“更新已有工程”模式。该阶段可以引入 Project Binding、工程扫描、existingResource 复用、所有权和增量更新语义，但不得反向改变新建工程的自包含契约。
