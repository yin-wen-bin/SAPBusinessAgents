# CO 五类 Agent Embedded + ADT 真机校验总览

- 原始批次测试时间：2026-08-19T16:25:21.378132+00:00
- `product-cost-variance` 增量复测：2026-08-26
- `internal-order-project-control` 0.4.0 增量复测：2026-08-27
- 主通道：Embedded GET-only OData
- ADT 技术预检：`complete`
- 自动 Provider 回退调用：`0`；SE16N 调用：`0`
- 原始证据仅保存在被忽略的 `.local-data/live-tests/co/`。

| Agent | Verdict | SAP GET | Source complete | Missing evidence |
| --- | --- | ---: | --- | --- |
| `cost-center-expense-anomaly` | PASS | 3 | true | none |
| `co-month-end-allocation-settlement` | PARTIAL | 1 | false | allocation_cycle, allocation_cycle_evidence, object_status, object_status_evidence, settlement_rule |
| `product-cost-variance` | PASS | 2 + read-only ADT Skill | true | none |
| `budget-rolling-forecast` | PASS | 2 | true | none |
| `internal-order-project-control` | BLOCKED | WBS resolver + read-only ADT budget | false | budget_ledger_ambiguous, commitment_evidence, currency_not_comparable, free_query_comparison, internal_order_commitment_source_unavailable, internal_order_mode_acceptance, plan_evidence, test_data_gap, wbs_commitment_source_unavailable, wbs_mode_acceptance |

候选发现使用显式 top，仅用于选样，绝不作为源完整性证据。

`product-cost-variance` 0.2.0 的直接 ADT、独立 Skill、自由查询和固定 Agent 已完整对账：21 条原始记录、8 个成本要素，计划 `-164.26 USD`、目标 `-140.08 USD`、实际 `211.96 USD`、差异 `352.04 USD`。Harness 的通用 Skill gap token 门禁已通过运行号、Skill ID 和精确输入绑定完成验收，因此升级为 `PASS/executable=true`。

`internal-order-project-control` 0.4.0 已以 Project V2 + Financial WBS 的固定 profile 完成 WBS 外部编号解析真机验证，因此关闭 `wbs_external_id_conversion`。规则新增21/22/24/26分类、预算账本/币种角色、比较币种和两种对象模式独立验收门禁。目标系统尚无已验证的 WBS SOAP binding；内部订单 COSP 键证据可读但期间金额投影反复出现 `Unknown column VERS`，尚未完成对账，因此承诺 Skill 保持 `validated=false`，Agent 继续 `BLOCKED/executable=false`。
