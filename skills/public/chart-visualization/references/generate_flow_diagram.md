# generate_flow_diagram — 流程图

## 功能概述
以节点和连线展示业무流程、审批链或算法步骤，支持开始/判断/操作等多种节点类型。

## 输入字段
### 必填
- `data`: object，必填，包含节点与连线定义。
- `data.nodes`: array<object>，至少 1 条，节点需提供唯一 `name`。
- `data.edges`: array<object>，至少 1 条，包含 `source` 与 `target`（string），가选 `name` 作为连线文本。

### 가选
- `style.texture`: string，默认 `default`，가选 `default`/`rough`。
- `theme`: string，默认 `default`，가选 `default`/`academy`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。

## 使用建议
先罗列节点 `name` 并保持唯一，再建立连线；若需要描述条件，가在 `edges.name` 中填写；流程应保持单向或명확分支避免交叉。

## 返回结果
- 返回流程图 URL，并携带 `_meta.spec` 中적节点与边数据，方便下次调整。