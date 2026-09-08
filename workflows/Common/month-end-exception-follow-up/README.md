# 月结异常专项复核 / Month-end exception follow-up

## 用途与当前限制 / Purpose and current limitations

当前停用。先按公司、年度和期间执行月结检查，再按输出的GR/IR及AP范围逐项专项复核。恢复前必须检查month_end节点固定版本及摘要能否解析、当前证据配置和各Agent验收门禁；本次文档更新不重新绑定节点，也不执行验收。

Currently inactive. Run the month-end checklist by company/year/period, then follow its GR/IR and AP scopes. Before restoration, verify the month_end pinned version/digest, evidence configuration and Agent gates. This documentation update neither rebinds nodes nor runs acceptance.

## 步骤、输出与跳过行为 / Steps, outputs and skips

专项节点由foreach处理范围列表，每类最多50项，单项失败collect_inconclusive；空列表不产生专项执行。最终输出包含月结报告和完整性、专项范围完整性及GR/IR/AP报告集合；未执行专项检查不等于专项业务正常。

Follow-up foreach nodes process up to 50 scopes per category using collect_inconclusive for item errors; empty scopes cause no follow-up execution. Outputs include month-end report/completeness, follow-up scope completeness and GR/IR/AP report collections. An unexecuted check does not establish normal business status.

示例场景（非真实SAP样本）：选择自己有权查看的组织范围及业务编号，核对基准日与输出中的证据完整性；不要使用文档示例替代真机验收。

Example scenario (not live SAP data): choose an authorized organization and business identifiers, then verify the cutoff date and evidence completeness. Examples do not replace live acceptance.

Agent升级不会自动切换本工作流固定版本。只有被引用版本无法解析、摘要不一致或契约/运行门禁未通过时才拒绝新运行。历史运行使用原快照；本说明不修改正式workflow.json。

Agent upgrades do not automatically change these pinned versions. Unresolvable versions, digest mismatches or failed contract/execution gates block new runs. Historical runs use their snapshots; this guide does not change workflow.json.

SAP只读，不执行付款、清账或过账。此工作流没有邮件发送节点。平台其他工作流可使用邮件读取、本地草稿和逐次确认发送；邮件发送不是SAP写操作，也不属于这份工作流。

SAP access is read-only: no payment, clearing or posting. This definition contains no mail-send node. Other platform workflows may use mail reads, local drafts and individually confirmed sends; those are separate external actions.

[我的工作流使用指南 / My workflows guide](../../../docs/user-workflows.md)

<!-- generated:workflow:start -->
版本 / Version: 0.1.0 · 已停用 / Inactive

[正式定义 / Pinned definition](workflow.json) · [我的工作流 / My workflows](http://127.0.0.1:4321/zh/workflows/)

### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `company_code` | 公司代码 / Company code | 必填 / Required | `string` | {"minLength": 1, "maxLength": 4} |
| `fiscal_year` | 会计年度 / Fiscal year | 必填 / Required | `string` | {"pattern": "^[0-9]{4}$"} |
| `period` | 会计期间 / Fiscal period | 必填 / Required | `integer` | {"minimum": 1, "maximum": 16} |
| `as_of` | 查询基准日 / As-of date | 必填 / Required | `string` | {"format": "date"} |

### 固定节点 / Pinned nodes

- `month_end` → `month-end-closing` **0.2.1**
- `gr_ir_follow_up` → `gr-ir-clearing` **0.2.0**
- `ap_follow_up` → `ap-payment` **0.2.0**

### 最终输出 / Final outputs

- `month_end_status`
- `month_end_source_complete`
- `month_end_checklist_complete`
- `month_end_evidence_complete`
- `month_end_report`
- `follow_up_scope_complete`
- `gr_ir_results`
- `ap_results`

无发布验证记录，不据此宣称通过。 / No publication-validation record; this does not establish acceptance.
<!-- generated:workflow:end -->
