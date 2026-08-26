# CO 五类 Agent Embedded + ADT 真机校验总览

- 原始批次测试时间：2026-08-19T16:25:21.378132+00:00
- `product-cost-variance` 增量复测：2026-08-25
- 主通道：Embedded GET-only OData
- ADT 技术预检：`complete`
- 自动 Provider 回退调用：`0`；SE16N 调用：`0`
- 原始证据仅保存在被忽略的 `.local-data/live-tests/co/`。

| Agent | Verdict | SAP GET | Source complete | Missing evidence |
| --- | --- | ---: | --- | --- |
| `cost-center-expense-anomaly` | PASS | 3 | true | none |
| `co-month-end-allocation-settlement` | PARTIAL | 1 | false | allocation_cycle, allocation_cycle_evidence, object_status, object_status_evidence, settlement_rule |
| `product-cost-variance` | BLOCKED | 2 + read-only ADT Skill | true | free_query_skill_execution |
| `budget-rolling-forecast` | PASS | 2 | true | none |
| `internal-order-project-control` | PARTIAL | 4 | false | budget, commitment, control_object_not_found, master, master_evidence |

候选发现使用显式 top，仅用于选样，绝不作为源完整性证据。

`product-cost-variance` 0.2.0 的直接 ADT、独立 Skill 和固定 Agent 已完整对账：21 条原始记录、8 个成本要素，计划 `-164.26 USD`、目标 `-140.08 USD`、实际 `211.96 USD`、差异 `352.04 USD`。自由查询的 Skill 调用仍被 Harness gap token 门禁阻止，因此保持 `BLOCKED/executable=false`。
