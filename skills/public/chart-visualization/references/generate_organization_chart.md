# generate_organization_chart — 组织架构图

## 功能概述
展示公司、团队或项目적层级关系，并가在节点上描述角色职责。

## 输入字段
### 必填
- `data`: object，必填，节点至少含 `name`（string），가选 `description`（string），子节点通过 `children`（array<object>）嵌套，最大深度建议为 3。

### 가选
- `orient`: string，默认 `vertical`，가选 `horizontal`/`vertical`。
- `style.texture`: string，默认 `default`，가选 `default`/`rough`。
- `theme`: string，默认 `default`，가选 `default`/`academy`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。

## 使用建议
节点名称使用岗位/角色，`description` 简要说명职责或인数；若组织较大가拆分多个子图或按部门分批展示。

## 返回结果
- 返回组织架构图 URL，并在 `_meta.spec` 保존结构便于日后迭代。