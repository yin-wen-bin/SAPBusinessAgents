# 银行来款与应收核销核对助手 / AR Cash Application Reconciliation Assistant

## 用途与使用场景 / Purpose and scenario

核对银行来款、客户子分类账、清账凭证和发票的关系。先看来款搜索状态，再区分已确认关系、待销账和候选匹配。

Reconcile bank receipts with customer subledger payments, clearing documents and invoices. Read receipt-search status first, then distinguish confirmed links, pending application and candidates.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

查询日期指价值日，最多31天。参考号只在安全参数中输入。受限明细须主动揭示，下载与删除均受保护；默认保留30天。候选不是已清账，FI清账不是独立银行到账证据；不执行过账或销账。

Dates are value dates, with a maximum 31-day range. Enter references only through secure parameters. Explicitly reveal protected details; downloads and deletion are guarded, with 30-day default retention. Candidates are not cleared invoices, and FI clearing is not independent bank-settlement evidence; no posting or clearing is performed.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.1.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/FI/ar-cash-application/) · [en](http://127.0.0.1:4321/en/agents/FI/ar-cash-application/)

验收模式 / Acceptance mode: `three_stage` · 原记录日期 / Recorded date: 2026-09-05T17:00:15.636012+08:00
证据范围 / Evidence scope: `bounded`
日期范围查询的零来款、已过账来款、客户子分类账、确认清账、待销账和受限制品前台路径均已通过独立基线、自由查询及固定Agent对比。
Date-range complete-zero, posted-receipt, customer-subledger, confirmed-clearing, pending-application, and restricted-artifact frontend paths passed the independent baseline, free-query, and fixed-Agent comparisons.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `date_from` | 价值日起始日 / Value date from | 必填 / Required | `string` | {"format": "date", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |
| `date_to` | 价值日截止日 / Value date to | 必填 / Required | `string` | {"format": "date", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 来源来款数 / Source receipt count
- 已核对来款数 / Materialized receipt count
- 未解决来款数 / Unresolved receipt count
- 已确认关联来款数 / Confirmed receipts
- 需处理来款数 / Receipts requiring attention
- 无法确认来款数 / Inconclusive receipts
- 来款检索状态 / Receipt search status
- 来款核销状态 / Cash application status
- 逐笔来款核对结果 / Receipt results
- 受限银行明细 / Restricted bank details
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Evidence completeness
- 业务状态 / Business status
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
