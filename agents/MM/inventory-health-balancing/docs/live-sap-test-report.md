# 库存健康与库存平衡：真机测试报告

## 结论 / Verdict

- **BLOCKED**（测试时间：2026-08-17，证据范围：bounded）
- Embedded Provider 对精确物料、工厂和库存地点的当前库存 GET 完整返回；移动核心记录也可读。
- 物料凭证项目 API 不提供过账日期，目标系统未提供清单所用 Batch Plant 实体；ADT profile 又未配置。因此慢动/呆滞天数、临期批次和确定调拨量全部保持 **INCONCLUSIVE**。

## Embedded API evidence

| Scope | Result | Rows | Completeness |
| --- | --- | ---: | --- |
| Current stock, exact material + plant + storage location | GET completed | 1 | `source_complete=true` |
| Movement core, same exact scope | GET completed | 20 | `source_complete=true`; posting date absent |
| Batch/expiry entity | Schema unavailable | — | incomplete |

MB5B 未触发：当前阻塞点是表级效期/日期补证，不需要用事务报表替代 ADT。

## ADT fallback evidence

- Skill: `sap-adt-table-export`
- Object: `MCHA`; fields restricted to material, plant, batch, expiry date
- Filter scope: exact material + plant; `max_rows=2`; stable ascending key
- Result: `status=failed`, `read_only=true`, `validated=false`, `row_count=0`, reason `unsupported_system`
- Manifest SHA-256: `69ac50795f2ec5abb8030f22b51371ad5d6e96812146d29ba757b1653bb6c1cf`

## English summary

Current stock and movement-core GETs were complete for the bounded sample, but posting dates and batch-expiry evidence were unavailable from the released APIs. The protected ADT profile is not configured. Slow-moving, obsolete, expiry, and transfer-quantity conclusions are therefore suppressed. This is a configuration gap, not a SAPSkillhub defect.
