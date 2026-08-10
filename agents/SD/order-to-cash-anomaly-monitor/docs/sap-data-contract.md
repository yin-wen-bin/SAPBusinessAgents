# SAP数据契约

## 输入边界

- 根对象必须包含 `metadata`、`scope` 和 `records`。
- `metadata.schema_version` 固定为 `1.0`；真机evidence必须声明 `read_only=true`。
- 每次查询记录数据来源、完整分页状态、validation issues和脱敏run id。

## Thin SAPClaw服务

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`
- `API_BILLING_DOCUMENT_SRV`
- `API_OPLACCTGDOCITEMCUBE_SRV`

## 关联与质量规则

- 使用业务键串联对象，不依赖行顺序或展示文本。
- 数量、金额、币种和单位缺失或冲突时返回 `attention` 或 `blocked`，不得按零值处理。
- 冲销、取消、贷项和重复引用必须保留为显式证据。
- SAPSkillhub只允许补充标准OData未提供的只读证据。

## 输出

统一输出 `status`、`score`、`findings`、`blockers`、`recommended_actions`、`evidence_refs`、`data_sources`、`completeness` 和分页状态。
