# GR/IR 清账助手 / GR/IR Clearing Assistant

## 用途与使用场景 / Purpose and scenario

复核采购订单的收货与发票收货差异，定位数量、金额和凭证链缺口，形成后续人工核对清单。

Review goods-receipt/invoice-receipt differences for a purchase order, identify quantity, amount and document-chain gaps, and prepare manual follow-up.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

先查看差异分类及其关联收货、发票和FI明细。建议不等于已执行清账或MR11调整；历史fixture命令不能替代网页生产执行。

Inspect discrepancy categories alongside receipt, invoice and FI evidence. Advice does not execute clearing or MR11 adjustments; historical fixtures do not replace production web execution.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/FI/gr-ir-clearing/) · [en](http://127.0.0.1:4321/en/agents/FI/gr-ir-clearing/)

验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-08-29T05:34:59.867989+00:00
证据范围 / Evidence scope: `complete`
独立直连基线、自由查询和固定 Agent 的 GR/IR v2 业务语义一致。
The independent direct-SAP baseline, free query, and fixed Agent are semantically consistent for GR/IR v2.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `gl_account` | GR/IR 总账科目 / GR/IR G/L account | 必填 / Required | `string` | {"minLength": 1, "maxLength": 10, "pattern": "^[0-9]+$"} |
| `date_from` | 开始日期 / Start date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |
| `date_to` | 截止日期 / End date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 公司代码 / Company code
- GR/IR 总账科目 / GR/IR G/L account
- 开始日期 / Start date
- 截止日期 / End date
- 业务状态 / Business status
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Business-evidence completeness
- 分析基准日期 / Analysis date
- 已检查项目数 / Items examined
- 已匹配项目数 / Items matched
- 待处理项目数 / Items requiring follow-up
- 无法确认项目数 / Inconclusive items
- 待处理记录 / Follow-up records
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
