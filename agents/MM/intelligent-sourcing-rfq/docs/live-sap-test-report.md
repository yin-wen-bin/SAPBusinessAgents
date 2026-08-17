# 智能寻源与 RFQ 评估：真机测试报告

## 结论 / Verdict

- **BLOCKED**（测试时间：2026-08-17，证据范围：bounded）
- Embedded Provider 的 RFQ 和报价核心实体、字段与 GET 路径已通过实时 schema 验证。
- 精确 RFQ 测试范围完整返回 0 行；这只能说明该范围内未返回测试数据，不能证明不存在其他 RFQ。
- 供应商、价格单位、交期、状态和历史货源仍需补证，而 ADT profile 未配置，因此禁止统一排名。

## Embedded API evidence

| Scope | Result | Rows | Completeness |
| --- | --- | ---: | --- |
| RFQ core, exact RFQ + purchasing organization | GET completed | 0 | `source_complete=true` |
| Supplier quotation core, exact RFQ | GET completed | 0 | `source_complete=true` |
| Supplier/source comparison fields | API schema gap | — | incomplete |

## ADT fallback evidence

- Skill: `sap-adt-table-export`
- Object: `EKKO`; fields restricted to document key/category/purchasing organization
- Filter scope: exact RFQ + purchasing organization; `max_rows=2`; stable ascending key
- Result: `status=failed`, `read_only=true`, `validated=false`, `row_count=0`, reason `unsupported_system`
- Manifest SHA-256: `4f2a792490fea86c87f3be492214d82cf624b4b93a6d30239208896220e8f9b1`

## English summary

Live metadata and GET execution succeeded for RFQ and quotation core fields, but the exact test RFQ returned no rows. Required comparison and eligibility fields remain incomplete, and the protected ADT profile is not configured. The 60/25/15 ranking is therefore suppressed. Empty business data is an environment/test-data gap and does not justify an Issue.
