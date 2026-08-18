# CO 五类 Agent Embedded + ADT 真机校验总览

- 测试时间：2026-08-17T11:41:06.839334+00:00
- 主通道：Embedded GET-only OData
- ADT 技术预检：`complete`
- SE16N 调用：`0`
- 原始证据仅保存在被忽略的 `.local-data/live-tests/co/`。

| Agent | Verdict | SAP GET | Source complete | Missing evidence |
| --- | --- | ---: | --- | --- |
| `cost-center-expense-anomaly` | PASS | 3 | true | none |
| `co-month-end-allocation-settlement` | PARTIAL | 1 | false | allocation_cycle, allocation_cycle_evidence, object_status, settlement_rule |
| `product-cost-variance` | PARTIAL | 2 | false | standard_cost, standard_cost_evidence |
| `budget-rolling-forecast` | PASS | 2 | true | none |
| `internal-order-project-control` | PARTIAL | 4 | true | budget, commitment, control_object_not_found, master |

候选发现使用显式 top，仅用于选样，绝不作为源完整性证据。
