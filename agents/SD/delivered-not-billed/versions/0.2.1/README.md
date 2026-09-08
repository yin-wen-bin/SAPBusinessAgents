# 已发货未开票监控 / Delivered-not-Billed Monitor

## 用途与使用场景 / Purpose and scenario

在已交货记录中找出尚未完成开票的项目，查看数量、日期与未开票账龄。

Identify delivered items not yet fully billed and inspect quantities, dates and unbilled aging.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

交货、PGI和开票分别解释。验收模式以原记录为准，不把确定性验收改称三级验收；本助手不会自动开票。

Distinguish delivery, PGI and billing. Preserve the recorded acceptance mode rather than calling deterministic acceptance three-stage; no invoices are automatically created.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.1** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/SD/delivered-not-billed/) · [en](http://127.0.0.1:4321/en/agents/SD/delivered-not-billed/)

验收模式 / Acceptance mode: `deterministic_runtime` · 原记录日期 / Recorded date: 2026-09-06T15:25:40+08:00
证据范围 / Evidence scope: `complete`
复用 0.2.0 验收；文案复核不代表重新查询 SAP。 / Acceptance reused from 0.2.0; documentation review is not a new SAP test.
实时Schema、Embedded直接GET基线与固定Agent的项目级业务结果一致；部分开票、取消和超量开票状态本次未观测。
Live schema, the direct Embedded GET baseline, and the fixed Agent agree at item grain; partial billing, cancellation, and overbilling were not observed in this run.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `sales_organization` | 销售组织 / Sales organization | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `date_from` | 开始日期 / Start date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |
| `date_to` | 结束日期 / End date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$", "x-sapba-server-default": "business_date"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 销售组织 / Sales organization
- 开始日期 / Start date
- 结束日期 / End date
- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [docs/sap-data-contract.md](docs/sap-data-contract.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
