# P2P 批量付款复核 / P2P batch payment review

## 用途与当前限制 / Purpose and current limitations

当前已停用，不接受新运行。用于对1–50张采购订单先读取P2P证据，再按公司与供应商复核付款准备度；不是自动付款流程。与付款准备度工作流（workflow-ec0e3072）的读取与AP复核链相近，但本定义额外汇总batch_status及整体完整性，不是其自动升级版本。

Currently inactive and rejects new runs. It traces 1–50 purchase orders, then reviews payment readiness by company and supplier; it does not make payments. Its chain resembles workflow-ec0e3072, but adds aggregate batch_status/completeness and is not an automatic upgrade of that workflow.

## 步骤、输出与跳过行为 / Steps, outputs and skips

P2P产生非空ap_payment_scopes时才执行AP；为空时AP返回显式inconclusive/not_assessed与证据缺口，不应显示已付款。节点失败时查看节点错误及已保留证据，不把失败结果当作零。

AP runs only for non-empty ap_payment_scopes. Otherwise it returns explicit inconclusive/not_assessed and gaps, never a paid conclusion. Inspect node failures and preserved evidence; failures are not zero records.

示例场景（非真实SAP样本）：选择自己有权查看的组织范围及业务编号，核对基准日与输出中的证据完整性；不要使用文档示例替代真机验收。

Example scenario (not live SAP data): choose an authorized organization and business identifiers, then verify the cutoff date and evidence completeness. Examples do not replace live acceptance.

Agent升级不会自动切换本工作流固定版本。只有被引用版本无法解析、摘要不一致或契约/运行门禁未通过时才拒绝新运行。历史运行使用原快照；本说明不修改正式workflow.json。

Agent upgrades do not automatically change these pinned versions. Unresolvable versions, digest mismatches or failed contract/execution gates block new runs. Historical runs use their snapshots; this guide does not change workflow.json.

SAP只读，不执行付款、清账或过账。此工作流没有邮件发送节点。平台其他工作流可使用邮件读取、本地草稿和逐次确认发送；邮件发送不是SAP写操作，也不属于这份工作流。

SAP access is read-only: no payment, clearing or posting. This definition contains no mail-send node. Other platform workflows may use mail reads, local drafts and individually confirmed sends; those are separate external actions.

[我的工作流使用指南 / My workflows guide](../../../docs/user-workflows.md)

<!-- generated:workflow:start -->
版本 / Version: 1.2.0 · 已停用 / Inactive

[正式定义 / Pinned definition](workflow.json) · [我的工作流 / My workflows](http://127.0.0.1:4321/zh/workflows/)

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `purchase_orders` | 采购订单号（最多 50 张） / Purchase orders (up to 50) | 必填 / Required | `array` | {"minItems": 1, "maxItems": 50} |
| `as_of` | 查询基准日 / As-of date | 必填 / Required | `string` | {"format": "date", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"} |

### 固定节点 / Pinned nodes

- `p2p` → `procure-to-pay-status` **0.3.1**
- `ap` → `ap-payment` **0.2.0**

### 最终输出 / Final outputs

- `p2p_details`
- `ap_details`
- `ap_status`
- `payment_run_evidence_complete`
- `bank_master_evidence_complete`
- `bank_settlement_evidence_complete`
- `bank_settlement_status`
- `ap_business_report`
- `batch_status`
- `source_complete`
- `evidence_complete`

无发布验证记录，不据此宣称通过。 / No publication-validation record; this does not establish acceptance.
<!-- generated:workflow:end -->
