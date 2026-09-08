# 采购到付款准备度复核 / Procure-to-pay readiness review

## 用途与当前限制 / Purpose and current limitations

本工作流发布验证结论是inconclusive，不是PASS。用户已接受的缺口为bank_settlement_not_proven（未证明银行扣款）及payment_run_and_bank_master_evidence（付款运行与银行主数据证据不足）。可以保留逐单及逐供应商已确认事实，但不能宣称付款或银行扣款完成。

Publication validation is inconclusive, not PASS. Accepted gaps are bank_settlement_not_proven and payment_run_and_bank_master_evidence. Confirmed PO/supplier facts remain useful but cannot prove completed payment or bank settlement.

## 步骤、输出与跳过行为 / Steps, outputs and skips

输入1–50张采购订单与基准日。先逐单读取采购、收货、发票与FI，再把类型化应付证据传给AP按公司与供应商分组复核。无AP分组时跳过并显式返回inconclusive/not_assessed；输出分别保留P2P与AP报告、状态及多类完整性，不把用户接受缺口改写为通过。

Supply 1–50 purchase orders and an as-of date. Trace PO/receipt/invoice/FI evidence, then pass typed AP scopes for company/supplier review. No AP scopes produces an explicit inconclusive/not_assessed skip. Final outputs preserve separate P2P/AP reports, states and completeness; acknowledging gaps does not turn them into PASS.

示例场景（非真实SAP样本）：选择自己有权查看的组织范围及业务编号，核对基准日与输出中的证据完整性；不要使用文档示例替代真机验收。

Example scenario (not live SAP data): choose an authorized organization and business identifiers, then verify the cutoff date and evidence completeness. Examples do not replace live acceptance.

Agent升级不会自动切换本工作流固定版本。只有被引用版本无法解析、摘要不一致或契约/运行门禁未通过时才拒绝新运行。历史运行使用原快照；本说明不修改正式workflow.json。

Agent upgrades do not automatically change these pinned versions. Unresolvable versions, digest mismatches or failed contract/execution gates block new runs. Historical runs use their snapshots; this guide does not change workflow.json.

SAP只读，不执行付款、清账或过账。此工作流没有邮件发送节点。平台其他工作流可使用邮件读取、本地草稿和逐次确认发送；邮件发送不是SAP写操作，也不属于这份工作流。

SAP access is read-only: no payment, clearing or posting. This definition contains no mail-send node. Other platform workflows may use mail reads, local drafts and individually confirmed sends; those are separate external actions.

[我的工作流使用指南 / My workflows guide](../../../docs/user-workflows.md)

<!-- generated:workflow:start -->
版本 / Version: 1.0.0 · 使用中 / Active

[正式定义 / Pinned definition](workflow.json) · [我的工作流 / My workflows](http://127.0.0.1:4321/zh/workflows/)

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `purchase_orders` | 采购订单号列表 / Purchase orders | 必填 / Required | `array` | {"minItems": 1, "maxItems": 50} |
| `as_of` | 查询基准日 / As-of date | 必填 / Required | `string` | {"format": "date", "minLength": 1, "maxLength": 10, "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |

### 固定节点 / Pinned nodes

- `trace_procure_to_pay_status` → `procure-to-pay-status` **0.3.1**
- `review_ap_payment_readiness` → `ap-payment` **0.2.0**

### 最终输出 / Final outputs

- `trace_procure_to_pay_status_po_results`
- `trace_procure_to_pay_status_ap_payment_scopes`
- `trace_procure_to_pay_status_business_status`
- `trace_procure_to_pay_status_source_complete`
- `trace_procure_to_pay_status_evidence_complete`
- `trace_procure_to_pay_status_business_report`
- `review_ap_payment_readiness_scope_results`
- `review_ap_payment_readiness_business_status`
- `review_ap_payment_readiness_source_complete`
- `review_ap_payment_readiness_evidence_complete`
- `review_ap_payment_readiness_payment_run_evidence_complete`
- `review_ap_payment_readiness_bank_master_evidence_complete`
- `review_ap_payment_readiness_bank_settlement_evidence_complete`
- `review_ap_payment_readiness_bank_settlement_status`
- `review_ap_payment_readiness_business_report`

发布验证 / Publication validation: `inconclusive` · 2026-08-30T12:17:44.501345+00:00

[发布验证记录（保留原结论） / Publication validation (original verdict preserved)](validation.json)
- `bank_settlement_not_proven`
- `payment_run_and_bank_master_evidence`
<!-- generated:workflow:end -->
