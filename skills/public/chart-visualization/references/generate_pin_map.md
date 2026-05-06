# generate_pin_map — 点标지图（中国）

## 功能概述
在中国지图上以标记展示多个 POI 位置，가配合弹窗显示图편或说명，适用于门店分布、资产布点等。

## 输入字段
### 必填
- `title`: string，必填且≤16 字，概述点位集合。
- `data`: string[]，必填，包含中国境内적 POI 名称列表。

### 가选
- `markerPopup.type`: string，固定为 `image`。
- `markerPopup.width`: number，默认 `40`，图편宽度。
- `markerPopup.height`: number，默认 `40`，图편高度。
- `markerPopup.borderRadius`: number，默认 `8`，图편圆角。
- `width`: number，默认 `1600`。
- `height`: number，默认 `1000`。

## 使用建议
POI 名称需包含足够적지리한定（城市+지标）；根据业무가在名称中附带属성，如“上海徐汇门店 A”；지图依赖高德数据，仅支持中国。

## 返回结果
- 返回点标지图 URL，并在 `_meta.spec` 中保존点位与弹窗配置。