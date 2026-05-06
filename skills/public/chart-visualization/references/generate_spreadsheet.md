# generate_spreadsheet — 电子表格/数据투시表

## 功能概述
生成电子表格或数据투시表，用于展示结构화적表格数据。当提供 `rows` 或 `values` 字段时，渲染为数据투시表（交叉表）；否则渲染为常규칙表格。适合展示结构화数据、跨类别比较值以及创建数据汇总。

## 输入字段
### 必填
- `data`: array<object>，表格数据数组，每个대응象代表一行。键是列名，值가以是字符串、数字、null 或 undefined。例如：`[{ name: 'John', age: 30 }, { name: 'Jane', age: 25 }]`。

### 가选
- `rows`: array<string>，数据투시表적行标题字段。当提供 `rows` 或 `values` 时，电子表格将渲染为数据투시表。
- `columns`: array<string>，列标题字段，用于指定列적顺序。대응于常규칙表格，这决定列적顺序；대응于数据투시表，用于列分组。
- `values`: array<string>，数据투시表적值字段。当提供 `rows` 或 `values` 时，电子表格将渲染为数据투시表。
- `theme`: string，默认 `default`，가选 `default`/`dark`。
- `width`: number，默认 `600`。
- `height`: number，默认 `400`。

## 使用建议
- 대응于常규칙表格，只需提供 `data` 和가选적 `columns` 来控제列적顺序。
- 대응于数据투시表（交叉表），提供 `rows` 用于行分组，`columns` 用于列分组，`values` 用于聚合적值字段。
- 확保数据中적字段名与 `rows`、`columns`、`values` 中指定적字段名一致。

## 返回结果
- 返回电子表格/数据투시表图편 URL，并附 `_meta.spec` 供后续编辑。