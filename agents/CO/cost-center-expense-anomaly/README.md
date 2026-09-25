# 成本中心费用异常助手 / Cost Center Expense Anomaly Assistant

## 用途与使用场景 / Purpose and scenario

在费用复核时按成本中心、期间和计划类别比较实际与计划，查看差异金额及比例。

Compare cost-center actual and plan costs by period and planning category, focusing on amount and percentage variances.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

差异阈值可按业务范围输入。计划或分页不完整时先补齐证据，不把缺失值视为零。

Set the variance threshold for the business scope. Resolve missing plan or paging evidence first; unavailable values are not zero.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **1.0.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/CO/cost-center-expense-anomaly/) · [en](http://127.0.0.1:4321/en/agents/CO/cost-center-expense-anomaly/)

验收模式 / Acceptance mode: `three_stage` · 原记录日期 / Recorded date: 2026-09-22T15:08:09.656701+00:00
证据范围 / Evidence scope: `complete`
独立SAP基线、自由查询和固定Agent的业务语义一致。
The independent SAP baseline, free query, and fixed Agent are semantically consistent.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `fiscal_year` | 会计年度 / Fiscal Year | 必填 / Required | `string` | {"minLength": 4, "maxLength": 4, "pattern": "^[0-9]{4}$"} |
| `controlling_area` | 控制范围 / Controlling Area | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `cost_center` | 成本中心 / Cost Center | 必填 / Required | `string` | {"minLength": 1, "maxLength": 10, "pattern": "^[0-9A-Za-z_-]+$"} |
| `period_from` | 起始期间 / Period From | 必填 / Required | `string` | {"pattern": "^[0-9]{1,3}$"} |
| `period_to` | 结束期间 / Period To | 必填 / Required | `string` | {"pattern": "^[0-9]{1,3}$"} |
| `planning_category` | 计划类别 / Planning Category | 必填 / Required | `string` | {"minLength": 1, "maxLength": 10} |
| `variance_threshold_pct` | 差异阈值（%） / Variance Threshold Pct | 可选 / Optional | `number` | {"default": 20} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 规范业务记录 / Canonical business records
- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 证据完整性 / Evidence completeness
- 业务完整性 / Business completeness
- 实际金额 / Actual amount
- 计划金额 / Plan amount
- 差异金额 / Variance amount
- 差异比例 / Variance percentage
- 证据缺口代码 / Evidence gap codes
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/formal-acceptance.md)
- [docs/sap-data-contract.md](docs/sap-data-contract.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [rules.py](rules.py)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
