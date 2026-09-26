# HTTP 接口

所有请求与响应均为 JSON；时间一律为带偏移量的 ISO 8601 字符串。错误响应形如
`{"error": "<code>", "message": "<说明>"}`。

## 基础档案

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/units` | 登记责任单位 `{unit_ref, name}` |
| POST | `/subscriptions` | 订阅管理范围 `{subscription_id, unit_ref, scope_type, scope_value}`，`scope_type ∈ place_category / language / location_prefix` |
| POST | `/signs` | 登记标识 `{sign_ref, unit_ref, location_code, place_category, carrier_type}` |

## 发布包

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/packages` | 发布签名发布包：`{package_id, revision, title, effective_from, supersedes_package_id?, entries[], exceptions[], basis_documents[]}`；发布即计算差异并生成候选 |
| GET | `/packages` | 列出全部发布包（含状态与签名校验结果） |
| GET | `/packages/{id}` | 发布包详情：`signature_valid`、`status`、`deprecated_at` 等 |
| POST | `/packages/{id}/deprecate` | 记录废止 `{effective_at, note?}`（只追加，重复废止返回 409） |
| GET | `/packages/{id}/download?purpose=` | `history_query` 任意已发布包可下载；`new_sign` 仅限当前推荐包，旧版返回 409 |
| GET | `/packages/{id}/adoption` | 按修订汇总：受影响载体、责任单位、承诺期限、完成比例 |
| POST | `/packages/{id}/candidates` | 幂等补齐该包的整改候选 |

## 差异与候选

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/diffs?from_package=&to_package=` | 可复核的差异与影响清单（词条差异 + 受影响标识） |
| GET | `/candidates?status=&sign_ref=` | 候选列表 |
| POST | `/candidates/{id}/decision` | 专业决定 `{action: confirm\|dismiss, decided_by, note?}`；确认生成整改要求，决定不可更改 |
| GET | `/requirements?unit_ref=&status=&target_package_id=` | 整改要求列表 |
| POST | `/requirements/{id}/commitment` | 承诺期限 `{committed_deadline}` |

## 同步

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/sync/reports` | 批次上报 `{source_system, batch_key, events[]}`；事件：`{event_id, seq, reported_at, sign_ref, language_code, entry_key, translation_revision, package_id, translation}`。批次与事件幂等，序列号回退被拒绝 |

## 限时豁免

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/exemptions` | 申请 `{exemption_id, sign_ref, requirement_id?, reason, valid_from, valid_until, decided_by, note?}` |
| GET | `/exemptions?sign_ref=` / `/exemptions/{id}` | 豁免状态与完整决定历史 |
| POST | `/exemptions/{id}/approve` | 批准 `{decided_by, note?}` |
| POST | `/exemptions/{id}/revoke` | 撤销 `{decided_by, note?}` |
| POST | `/exemptions/{id}/expire` | 登记到期（派生到期后落痕） |

## 追溯与公开查询

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/signs/{sign_ref}/trace` | 标识全部版本轨迹，含每次引用发布包的当时合法性 |
| GET | `/signs/{sign_ref}/versions/{translation_revision}/package` | 从任一标识版本反查当时引用的发布包（含签名与状态） |
| GET | `/public/signs/{sign_ref}` | 公开查询：当前推荐译文与现场实际译文并排，`transitioning` 标注换版中差异 |
