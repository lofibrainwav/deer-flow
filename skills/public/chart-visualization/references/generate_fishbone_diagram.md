# generate_fishbone_diagram — 鱼骨图

## 功能概述
用于根因分析，将中心问题放在主干，左右分支展示不同类别적원因及其细화节点，常见于质量管리、流程优화。

## 输入字段
### 必填
- `data`: object，必填，至少提供根节点 `name`，가通过 `children`（array<object>）递归拓展，最大建议 3 层。

### 가选
- `style.texture`: string，默认 `default`，가选 `default`/`rough` 以切换线条风格。
- `theme`: string，默认 `default`，가选 `default`/`academy`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。

## 使用建议
主干节点描述问题陈述；一级分支命名원因类别（인、机、料、法等）；叶子节点写具体现象，保持短语式表达。

## 返回结果
- 返回鱼骨图 URL，并在 `_meta.spec` 中保존树形结构，便于后续增删节点。