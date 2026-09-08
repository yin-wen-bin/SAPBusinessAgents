# 月结助手 / Month-end Closing Assistant

## 用途与使用场景 / Purpose and scenario

按公司、会计年度和期间检查月结证据，查看检查项结论、阻塞项与后续复核范围。

Inspect month-end evidence by company, fiscal year and period, including checklist results, blockers and follow-up scopes.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

当前是否可运行以生成状态区为准。公司配置和缺证项必须先完成复核；关账准备度建议不是已经完成月结，也不运行SAP关账程序。

The generated status below determines availability. Review company profiles and evidence gaps first; close-readiness advice is not a completed close and does not execute SAP closing programs.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.1** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/FI/month-end-closing/) · [en](http://127.0.0.1:4321/en/agents/FI/month-end-closing/)

验收模式 / Acceptance mode: `not_recorded` · 原记录日期 / Recorded date: 2026-09-08T09:01:54.436474+00:00
证据范围 / Evidence scope: `complete`
公司代码 1010、2026 年第 9 期的直连基线、自由查询和固定 Agent 已逐项匹配。12 项检查为 8 项通过、3 项需关注、1 项未评估；Agent 契约通过，但因外币评估运行状态缺少已审核来源，本次业务结论仍为 inconclusive。
For company code 1010, fiscal year 2026 period 9, the direct baseline, free query, and fixed Agent match check by check. Eight checks passed, three require attention, and one was not assessed. The Agent contract passed, while the business conclusion remains inconclusive because no reviewed FX valuation run-status source is available.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z]+$"} |
| `fiscal_year` | 会计年度 / Fiscal year | 必填 / Required | `string` | {"pattern": "^[0-9]{4}$"} |
| `period` | 会计期间 / Fiscal period | 必填 / Required | `integer` | {"minimum": 1, "maximum": 16} |
| `as_of` | 基准日 / As-of date | 必填 / Required | `string` | {"format": "date"} |
| `ledger` | 分类账（可选） / Ledger (optional) | 可选 / Optional | `string` | {"minLength": 1, "maxLength": 2} |
| `profile_id` | 公司月结配置（可选） / Company closing profile (optional) | 可选 / Optional | `string` | {"minLength": 1, "maxLength": 64, "pattern": "^[A-Za-z0-9._-]+$"} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 月结范围 / Closing scope
- 准备度状态 / Readiness status
- 查询源完整性 / Query-source completeness
- 检查清单完整性 / Checklist completeness
- 业务证据完整性 / Business-evidence completeness
- 12 项检查结果 / Twelve check results
- 异常 / Findings
- 责任待办 / Owner actions
- 缺失证据 / Missing evidence
- GR/IR 专项范围 / GR/IR follow-up scopes
- AP 专项范围 / AP follow-up scopes
- 不支持的专项复核 / Unsupported follow-ups
- 专项范围完整性 / Follow-up scope completeness
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/embedded-odata-live-acceptance.md)
- [docs/offline-regression.md](docs/offline-regression.md)
- [tests](tests)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
