# HIFI 替换验收记录

自动验收使用 `tests/fixtures/hifi_replacement`，从公开插件 API 完成旧工程上传、目标选择、Figma 选择上传、映射决策、候选构建、审核、哈希绑定批准和正式下载。机器结果见 [2026-09-15-hifi-replacement-acceptance.json](./2026-09-15-hifi-replacement-acceptance.json)。

## 自动验收结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 目标目录和根组件来自实际工程索引 | PASS | `MyVillage / Panel / Panel_MyVillage_Sketchboard` |
| HIFI 多、少、歧义对象均有处置 | PASS | 4 自动对应、1 人工改选、1 明确新增、2 保留、2 例外 |
| HIFI 图片材料实际替换 | PASS | `board_bg` 不再引用 Lowfi 资源；新 PNG 写入 `Img/HIFI` 并登记到 `package.xml`，字节与上传材料一致 |
| 原有对象 ID 保留 | PASS | 候选对象 ID 包含全部旧对象 ID |
| 共享组件引用保留 | PASS | `btn_next` 的共享资源 ID 未变化 |
| 修改范围受限 | PASS | 仅目标组件 XML、同 Package 的 `package.xml` 和派生 HIFI 图片发生变化 |
| 候选与正式交付一致 | PASS | SHA-256 `9e8f84de40016fb01fd3ca581112dc59e95e97f351e599443a620e2b76beeddb` |
| 错误候选哈希不能批准 | PASS | 服务端返回 `hifi_candidate_stale` |
| 对象级审核数据 | PASS | 5 修改、1 新增、2 保留、2 例外 |

## 自动化门

| 套件 | 结果 |
| --- | --- |
| Python 全量 | 1296 passed，4 skipped |
| Figma 插件 | 258 passed；构建一致性 5 passed |
| Web Console | 61 passed；TypeScript 与生产构建通过 |
| 插件纵向 Playwright | 1 passed，直接加载 `apps/figma-plugin/dist/ui.html` |
| 静态检查 | Ruff 通过；新增 Python 模块 strict mypy 通过；两端 TypeScript 通过 |

执行命令：

```powershell
$env:PYTHONPATH='src'
.venv\Scripts\python.exe scripts/run_hifi_replacement_acceptance.py `
  --workspace . `
  --output docs/validation/2026-09-15-hifi-replacement-acceptance.json `
  --candidate .test-tmp/hifi-replacement-candidate.zip
```

## FairyGUI Editor 验收

状态：**未执行**。

当前环境没有可用的 FairyGUI 6.1.4 可执行程序，因此不能宣称候选已在真实 Editor 中完成打开、保存、关闭、重开和再次保存。插件中的正式交付按钮要求用户下载候选并完成以下检查，检查记录会绑定候选 SHA-256 和 Editor 版本：

1. 布局和图层顺序。
2. 图片和共享组件引用。
3. Controller、Gear 和 Transition。

正式发布前仍需由安装了 FairyGUI 6.1.4 的测试者执行上述操作，并记录候选哈希 `9e8f84de40016fb01fd3ca581112dc59e95e97f351e599443a620e2b76beeddb`、测试时间、打开或保存弹窗及最终结果。
