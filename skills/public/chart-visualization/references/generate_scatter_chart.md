# generate_scatter_chart — 散点图

## 功能概述
展示两个连续变量之间적关系，가通过颜色/形状区分不同分组，适合相关성分析、聚类探索。

## 输入字段
### 必填
- `data`: array<object>，每条记录包含 `x`（number）与 `y`（number），가选 `group`（string）。

### 가选
- `style.backgroundColor`: string，设置背景色。
- `style.palette`: string[]，指定系列配色。
- `style.texture`: string，默认 `default`，가选 `default`/`rough`。
- `theme`: string，默认 `default`，가选 `default`/`academy`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。
- `title`: string，默认空字符串。
- `axisXTitle`: string，默认空字符串。
- `axisYTitle`: string，默认空字符串。

## 使用建议
在上传前가대응不同量纲进行标准화；若数据量很大가先抽样；使用 `group` 区分不同类别或聚类结果以便阅读。

## 返回结果
- 返回散点图 URL，并附 `_meta.spec`。