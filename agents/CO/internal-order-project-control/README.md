# 内部订单与项目控制助手 / Internal Order and Project Control Assistant

## 用途与使用场景 / Purpose and scenario

分别复核内部订单或WBS对象的实际、计划、预算与承诺，查看对象和台账是否能够唯一绑定。

Review actuals, plan, budget and commitments for an internal order or WBS, checking unique object and ledger bindings.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

不同台账、对象或币种不合计。完整预算与承诺路径及验收样本仍以阻塞区为准；不能用计划金额替代正式预算。

Do not aggregate ledgers, objects or currencies. The blocker list governs budget/commitment availability and acceptance; plan values are not a substitute for approved budget.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.4.0** · 使用中 / Active · 因证据或验收缺口受阻 / Blocked

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/CO/internal-order-project-control/) · [en](http://127.0.0.1:4321/en/agents/CO/internal-order-project-control/)

当前不能执行；完成证据与验收门禁后才可启用执行。 / Execution is blocked until evidence and acceptance gates pass.
验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-08-27T19:00:00+08:00
证据范围 / Evidence scope: `bounded`
0.4.0 已删除已解决的 WBS 外部编号转换缺口，并增加按21/22/24/26分类、预算账本/币种角色和两种对象模式独立验收门禁。目标系统尚未激活 WBS 标准承诺服务；内部订单 COSP/COSS 金额投影仍不稳定，预算权威账本、计划、币种可比性和四路径完整样本也尚未通过。
Version 0.4.0 removes the resolved WBS external-ID gap and adds 21/22/24/26 status, budget-ledger/currency-role evidence, and independent object-mode acceptance gates. The target WBS commitment service is not active; internal-order COSP/COSS amount projection remains unstable, and authoritative budget-ledger, plan, currency-comparability, and complete four-path samples have not passed.

当前阻塞项 / Current blockers:

- `budget_ledger_ambiguous`
- `budget_evidence`
- `commitment_evidence`
- `currency_not_comparable`
- `free_query_comparison`
- `plan_evidence`
- `test_data_gap`
- `wbs_mode_acceptance`
- `internal_order_mode_acceptance`
- `wbs_commitment_source_unavailable`
- `internal_order_commitment_source_unavailable`

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `fiscal_year` | 会计年度 / Fiscal Year | 必填 / Required | `string` | {"minLength": 4, "maxLength": 4, "pattern": "^[0-9]{4}$"} |
| `object_type` | 对象类型 / Object Type | 必填 / Required | `string` | {"pattern": "^(INTERNAL_ORDER\|WBS)$"} |
| `object_id` | 对象编号 / Object Id | 必填 / Required | `string` | {"minLength": 1, "maxLength": 24, "pattern": "^[0-9A-Za-z_./ -]+$"} |
| `planning_category` | 计划类别（可选） / Planning Category (optional) | 可选 / Optional | `string` | {"minLength": 1, "maxLength": 10} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 控制证据完整性 / Control-evidence completeness
- 已解析控制对象 / Resolved control object
- 实际成本 / Actual cost
- 计划成本 / Plan cost
- 预算 / Budget
- 承诺 / Commitments
- 预计完工成本 / Estimate at completion
- 剩余预算 / Remaining budget
- 预算消耗百分比 / Budget consumption percent
- 实际成本状态 / Actual-cost status
- 计划状态 / Plan status
- 预算状态 / Budget status
- 承诺状态 / Commitment status
- 按值类型的承诺状态 / Commitment status by value type
- 承诺币种角色 / Commitment currency role
- 预算账本 / Budget ledger
- 预算币种角色 / Budget currency role
- 比较币种 / Comparison currency
- 对象模式验收状态 / Object-mode acceptance status
- 证据缺口 / Evidence gaps
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [docs/sap-data-contract.md](docs/sap-data-contract.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
