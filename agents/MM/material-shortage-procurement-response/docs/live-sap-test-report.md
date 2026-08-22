# 外购件短缺采购响应：真机测试报告

> 历史报告：以下 `TG10` / 2026-08-17 结果仅保留用于审计，已被
> [`RM4_CP` 三级真机闭环报告](three-stage-live-acceptance.md)取代。当前 Agent
> 验证结论为 `PASS`，且 `executable=true`。

## 2026-08-18 契约解耦说明

SAPBusinessAgents 已停止传递或管理 ADT `connection_profile`。当前已安装的 `sap-adt-table-export` Schema 仍要求调用方传入该字段，因此新的 ADT 补证会在启动 Skill 前明确记录 `skill_contract_incompatible`，不会注入默认 profile，业务结论继续保持 **INCONCLUSIVE**。下方 2026-08-17 的 ADT 结果仅作为历史真机证据保留；新契约发布前不重新执行真机 ADT。

## 结论 / Verdict

- **BLOCKED**（测试时间：2026-08-17，证据范围：bounded）
- Embedded Provider 的真实 GET 连通，但 MRP Coverage 超时，且目标 API 不提供本流程所需的全部释放状态、计划交期和货源字段。
- `sap-adt-table-export` 因受保护 profile `mm-read-only` 未配置而安全失败；因此确定缺口、待释放 PR、催交和有效货源结论均保持 **INCONCLUSIVE**。

## Embedded API evidence

| Scope | Result | Rows | Completeness |
| --- | --- | ---: | --- |
| Purchase requisition core, exact material + plant | GET completed | 137 | `source_complete=true` |
| Purchase-order item core, exact material + plant | GET completed | 40 | `source_complete=true` |
| MRP Coverage, exact material + plant + MRP area + shortage profile/counter | Timeout | — | incomplete |

以上行数只描述测试过滤范围，不证明业务流程完成；公开报告不包含原始行或完整凭证号。

## ADT fallback evidence

- Skill: `sap-adt-table-export`
- Object: `EBAN`; fields restricted to PR key, material, plant
- Filter scope: exact material + plant; `max_rows=2`; stable ascending key
- Result: `status=failed`, `read_only=true`, `validated=false`, `row_count=0`, reason `unsupported_system`
- Manifest SHA-256: `c6d097e110d77cd2de82eb514e01a217dc25603ee4d4ed88923e53a0f8f0681d`

## English summary

Embedded GETs returned complete bounded PR and PO-item core evidence, while MRP Coverage timed out. The protected ADT profile is not configured, so the approved fallback failed closed. No shortage quantity or procurement action is confirmed. This is an environment/configuration gap and does not justify a SAPSkillhub Issue.
