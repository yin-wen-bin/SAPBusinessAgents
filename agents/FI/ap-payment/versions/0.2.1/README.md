# 应付账款付款助手 / AP Payment Assistant

## 用途与使用场景 / Purpose and scenario

在安排供应商付款前，查看未清项、到期日、付款冻结、折扣和重复付款候选；先按公司代码与供应商查询。

Before scheduling a vendor payment, inspect open items, due dates, payment blocks, discounts and possible duplicates; start with company code and supplier.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

付款准备度不表示已经付款。工作流传入的 P2P 证据是另一种入口，不要求用户在网页手工构造内部证据。

Payment readiness does not mean payment has occurred. Workflow-supplied P2P evidence is a separate entry point, not a manual web-form requirement.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.1** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/FI/ap-payment/) · [en](http://127.0.0.1:4321/en/agents/FI/ap-payment/)

验收模式 / Acceptance mode: `deterministic_runtime` · 原记录日期 / Recorded date: 2026-08-29T07:00:00+00:00
证据范围 / Evidence scope: `complete`
复用 0.2.0 验收；文案复核不代表重新查询 SAP。 / Acceptance reused from 0.2.0; documentation review is not a new SAP test.
既有业务语义验收继续有效；活跃查询路径已迁移为 Embedded OData，历史报告不声明重新执行了架构验收。
The existing business-semantic acceptance remains valid; the active query path is Embedded OData, while the historical report does not claim a rerun of architecture acceptance.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 可选 / Optional | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `supplier` | 供应商编号 / Supplier | 可选 / Optional | `string` | {"minLength": 1, "maxLength": 10, "pattern": "^[0-9A-Za-z_-]+$"} |
| `as_of` | 查询基准日 / As-of date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 查询模式 / Query mode
- 公司代码 / Company code
- 供应商编号 / Supplier
- 查询基准日 / As-of date
- 分组付款复核结果 / Payment review scope results
- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Business-evidence completeness
- 付款运行证据完整性 / Payment-run evidence completeness
- 银行主数据证据完整性 / Bank-master evidence completeness
- 银行扣款证据完整性 / Bank-settlement evidence completeness
- 银行扣款核验状态 / Bank-settlement verification status
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/p2p-evidence-workflow-live-acceptance.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
