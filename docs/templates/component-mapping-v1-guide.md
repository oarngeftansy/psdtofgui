# 通用组件映射 v1 填写说明

## 交付内容

请复制并填写 `component-mapping-v1-template.json`。`component-mapping-v1.json`
是一份完整示例，Markdown 是填写说明。

这是“需求收集与映射评审格式 v1”。需求方填写后，研发侧还会进行 schema
校验、FairyGUI 工程扫描和映射确认；不要把未经验证的 `candidate` 文件直接当作
生产映射导入。

## 映射链路

```text
Figma 组件及 Variant
→ UIR 通用语义
→ FairyGUI 组件名称、路径和 Controller 值
```

## 必填字段

| 字段 | 说明 | 示例 |
|---|---|---|
| `key` | 全局唯一、稳定的英文 key | `common_primary_button` |
| `figma.names` | Figma 组件的真实名称 | `[“通用一级按钮”]` |
| `uir.role` | 通用语义角色 | `button` |
| `uir.semanticName` | 通用语义名 | `primaryAction` |
| `fairygui.package` | FairyGUI 包名 | `Common` |
| `fairygui.component` | FairyGUI 组件名，不包含 ID | `Common_Btn_Primary` |
| `fairygui.path` | 组件 XML 的包内相对路径 | `Core/Button/Common_Btn_Primary.xml` |
| `source.owner` | 规则负责人或团队 | `UI需求组` |
| `status` | 需求方固定填写 | `candidate` |

## 属性与 Variant

Figma Variant 的每个属性和可选值都必须明确列出。

```json
"Size": {
  "required": false,
  "default": "L",
  "valueMap": {
    "L": {"controller": "size", "page": "0"},
    "M": {"controller": "size", "page": "1"},
    "S": {"controller": "size", "page": "2"}
  }
}
```

## 禁止填写真实 ID

不得在通用映射中填写：

```json
{
  "packageId": "qil5i1mk",
  "componentId": "v27f1nupomj"
}
```

真实 ID 必须由工具扫描当前 FairyGUI 工程后生成，不能作为通用映射数据。

## 降级策略

`fallback.mode` 第一版允许：

- `rasterSubtree`：目标组件缺失时，将最小安全子树导出为 PNG。
- `unsupported`：不允许降级，缺失时阻止生成。

## 交付时请同时提供

- Figma 组件库链接。
- FairyGUI 工程或组件目录。
- Figma Variant 属性和全部可选值。
- 对应的 FairyGUI Controller 名称和页面。
- 哪些组件允许 PNG 降级。
- 哪些组件缺失时必须阻止生成。

## 状态说明

需求方统一填写 `candidate`。工具扫描实际 FairyGUI 工程后才会生成：

- `verified`：目标组件和属性存在。
- `missing`：目标组件不存在，按 fallback 处理。
- `conflict`：存在重名、路径或类型冲突，需要人工确认。
