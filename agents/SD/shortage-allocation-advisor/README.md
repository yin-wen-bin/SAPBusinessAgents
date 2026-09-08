# 缺货分配建议 / Shortage Allocation Advisor

## 用途与使用场景 / Purpose and scenario

在供应不足时辅助复核销售需求与可用量证据，形成分配建议的评审依据。

Review sales demand and availability evidence during shortages as a basis for reviewing allocation proposals.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

ATP可用量证据未通过门禁时不能确认分配或承诺交货；不写分配结果、不进行正式ATP。

Without accepted ATP-availability evidence, allocations or delivery commitments cannot be confirmed; the Agent neither writes allocations nor performs formal ATP.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.1.0** · 使用中 / Active · 因证据或验收缺口受阻 / Blocked

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/SD/shortage-allocation-advisor/) · [en](http://127.0.0.1:4321/en/agents/SD/shortage-allocation-advisor/)

当前不能执行；完成证据与验收门禁后才可启用执行。 / Execution is blocked until evidence and acceptance gates pass.
验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-08-20T08:57:26.766047+00:00
证据范围 / Evidence scope: `bounded`
三级结果语义一致，但真实能力或证据缺口阻止执行。
The three stages are semantically consistent, but a live capability or evidence gap blocks execution.

当前阻塞项 / Current blockers:

- `atp_availability_evidence`

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `plant` | 工厂 / Plant | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `material` | 物料 / Material | 必填 / Required | `string` | {"minLength": 1, "maxLength": 40, "pattern": "^[0-9A-Za-z_-]+$"} |
| `date_from` | 开始日期 / Start date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |
| `date_to` | 结束日期 / End date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 工厂 / Plant
- 物料 / Material
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
