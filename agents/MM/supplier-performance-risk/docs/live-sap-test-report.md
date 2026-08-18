# 供应商绩效与交付风险：真机测试报告

## 2026-08-18 契约解耦说明

SAPBusinessAgents 已停止传递或管理 ADT `connection_profile`。当前已安装的 `sap-adt-table-export` Schema 仍要求调用方传入该字段，因此新的 ADT 补证会在启动 Skill 前明确记录 `skill_contract_incompatible`，不会注入默认 profile，业务结论继续保持 **INCONCLUSIVE**。下方 2026-08-17 的 ADT 结果仅作为历史真机证据保留；新契约发布前不重新执行真机 ADT。

## 结论 / Verdict

- **BLOCKED**（测试时间：2026-08-17，证据范围：bounded）
- Embedded Provider 对供应商/采购组织范围的 PO 与收货核心实体可读；最终清单已进一步限定到最长 365 天的 PO 日期范围，并以精确 PO 键读取收货。
- 标准 API 缺少可用于正式 OTIF 的完整 schedule line 与收货过账日期；ADT profile 未配置，因此正式 OTIF 被抑制。

## Embedded API evidence

| Scope | Result | Rows | Completeness |
| --- | --- | ---: | --- |
| PO core, exact supplier + purchasing organization | GET completed | 304 | `source_complete=true` |
| Receipt core, exact supplier | GET completed | 206 | `source_complete=true`; posting date absent |
| Supplier purchasing status | Timed out in probe | — | incomplete |

上述探针行数不等于最终 365 天样本量，也不得用来计算 OTIF；最终 Agent 使用 PO 日期边界和精确 PO 绑定。

## ADT fallback evidence

- Skill: `sap-adt-table-export`
- Object: `EKET`; fields restricted to PO/item/schedule key and delivery date
- Filter scope: one exact PO key; `max_rows=2`; stable ascending key
- Result: `status=failed`, `read_only=true`, `validated=false`, `row_count=0`, reason `unsupported_system`
- Manifest SHA-256: `7f56e64b33ce19cef58d14596f826f841c2eb73702b71ef74953d4be46281630`

## English summary

Live GETs confirmed readable PO and receipt core evidence, but complete schedule-line and receipt-date evidence is unavailable from the released APIs. The final Agent narrows PO scope to 365 days and binds receipt reads to exact PO keys. Because the protected ADT profile is missing, formal OTIF and delivery-risk conclusions remain inconclusive. This is a configuration gap, not a SAPSkillhub defect.
