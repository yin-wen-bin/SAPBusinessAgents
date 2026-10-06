# 应收账款催收助手 / AR Collection Assistant

## 用途与使用场景 / Purpose and scenario

按客户批量查看应收账龄，并定位具体年度、凭证和行项目。先看需要处理的工作清单，再查看全部未清明细；未到期项目仅监控，不混入催收待办。

Review aging by customer and identify the fiscal year, document and line item to act on. Start with the action worklist, then all open items; monitor-only items are not dunning tasks.

## 如何开始 / Getting started

从下方本地网页入口进入；先确认生命周期、验收状态和本机连接配置。填写公开输入后执行，技术字段名仅用于对照契约。示例场景（非真实SAP样本）：用本系统中具有查看权限的业务对象及适用日期运行一次，核对输入范围，再展开一条异常明细；请勿将示例当作测试数据或承诺的结果。

Open the local page below and confirm lifecycle, acceptance and connection readiness. Supply the public inputs; technical IDs help cross-reference the contract. Example scenario (not live SAP data): use a permitted business object or batch with applicable dates, verify scope, then inspect one exception. This is not a test dataset or a promised result.

## 结果解读与下一步 / Reading results and next steps

逐项查看原因、建议动作和 SAPBusinessAgents 处理优先级，下载 ar-collection-worklist.csv 分工跟进。优先核实证据缺口或冻结；付款、争议和承诺仅提示另行复核，不声称已核验。历史催收事件不等于历史主数据快照。

Inspect reasons, recommended actions and SAPBusinessAgents priority, then download ar-collection-worklist.csv for follow-up. Resolve evidence gaps or blocks first. Payments, disputes and promises require separate evidence. Historical dunning events are not historical master-data snapshots.

查询执行结束不等于业务完成。先看业务结论和证据缺口，再看数量、金额、币种与明细。缺证应补齐后复核，不以零值代替未知；不同币种不合计。建议由业务人员确认后在授权流程中处理，Agent不执行SAP写操作。

A completed query is not a completed business process. Review conclusions and gaps, then counts, amounts, currencies and detail. Resolve gaps before acting; unknown is not zero and currencies are not aggregated. Business staff act through authorized processes; the Agent performs no SAP writes.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **1.2.0** · 使用中 / Active · 验收通过 / Passed

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/FI/ar-collection/) · [en](http://127.0.0.1:4321/en/agents/FI/ar-collection/)

验收模式 / Acceptance mode: `deterministic_runtime` · 原记录日期 / Recorded date: 2026-09-06T02:17:45.011633+08:00
证据范围 / Evidence scope: `bounded`
1.2.0已通过独立SAP基线、候选固定Agent和中英文前台验收；工作清单、全部明细、Markdown与CSV保持一致。
Version 1.2.0 passed independent SAP baseline, candidate fixed-Agent, and bilingual frontend acceptance; worklist, full detail, Markdown, and CSV results are consistent.

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4, "pattern": "^[0-9A-Za-z_-]+$"} |
| `customers` | 客户编号 / Customers | 必填 / Required | `array` | {"minItems": 1, "maxItems": 50} |
| `as_of` | 查询基准日 / As-of date | 可选 / Optional | `string` | {"format": "date", "pattern": "^\\d{4}-\\d{2}-\\d{2}$", "x-sapba-server-default": "business_date"} |
| `dunning_area` | 催收区域（可选） / Dunning area (optional) | 可选 / Optional | `string` | {"minLength": 1, "maxLength": 4} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 请求客户数 / Requested customers
- 结果客户数 / Result customers
- 正常客户数 / Normal customers
- 需处理客户数 / Customers requiring attention
- 无法确认客户数 / Inconclusive customers
- 需要处理项目数 / Items requiring action
- 仅监控项目数 / Monitor-only items
- 逐客户结果 / Customer results
- 催收工作清单 / Collection worklist
- 查询源完整性 / Query-source completeness
- 业务证据完整性 / Evidence completeness
- 业务状态 / Business status
- 结构化业务报告 / Structured business report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [原始验收记录（适用范围以原报告为准） / Original acceptance record (original scope applies)](docs/three-stage-live-acceptance.md)
- [rules.py](rules.py)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
