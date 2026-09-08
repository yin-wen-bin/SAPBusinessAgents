# SAP数据契约

## 输入边界

- 根对象必须包含 `metadata`、`scope` 和 `records`。
- `metadata.schema_version` 固定为 `1.0`；真机evidence必须声明 `read_only=true`。
- 每次查询记录数据来源、完整分页状态、validation issues和脱敏run id。

## Embedded SAP Read Provider服务

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`
- `API_BILLING_DOCUMENT_SRV`

## 条件ADT证据

- 仅当Embedded查询完整、订单及项目存在且Released OData未暴露项目不完整状态时调用 `sap-adt-table-export`。
- 不完整日志对象固定为 `VBUV`；字段固定为 `VBELN`、`POSNR`、`ETENR`、`TBNAM`、`FDNAM`、`FEHGR`、`STATG`。这些字段已通过目标系统实时DDIC验证；未验证字段不得加入查询。
- 对本次实际出现的代码，使用 `TVFST`解析开票冻结文本、`TVLST`解析交货冻结文本、`DD07T`解析信用状态文本、`DD03T`解析表字段文本。`DD03T`完整零行时，使用`DD03L`取得数据元素并由`DD04T`解析文本。
- 每个文本对象均先执行精确英语代码预检（`max_rows=2`），正式查询只允许使用本次去重代码的`IN`过滤（`max_rows=200`）；不执行无过滤或通配查询。
- 预检按内部格式的订单号和首个项目号精确过滤，`max_rows=2`；正式查询按订单号过滤，`max_rows=200`。两次查询都不由调用方猜测 `order_by`，由Skill依据实时DDIC可信键强制升序稳定分页。
- VBUV按“每个缺失字段一行”的稀疏日志解释，不要求每个Embedded项目均有返回行。只有 `status=complete`、`read_only=true`、`validated=true`、分页和来源完整、无validation issue、Hash验证成功，并且所有返回订单/项目键均落在Embedded精确范围内时，才移除 `sales_order_item_incompletion_evidence`；完整零行明确表示该订单范围未记录缺失字段。
- `partial`、`failed`、缺行、重复/额外键或达到行数上限均保持 `inconclusive`；VBUV在精确范围内完整零行是“未记录缺失字段”的正面证据，但文本表零行必须继续走已批准的DDIC回退或记录`code_text_evidence`。

## 关联与质量规则

- 使用业务键串联对象，不依赖行顺序或展示文本。
- 数量、金额、币种和单位缺失或冲突时返回 `attention` 或 `blocked`，不得按零值处理。
- 冲销、取消、贷项和重复引用必须保留为显式证据。
- SAPSkillhub只允许补充标准OData未提供的只读证据；调用输入不得携带Profile、URL、客户端或凭据。

## 输出

业务记录保留原始 `billing_block_reason`、`delivery_block_reason`、`credit_status` 和 `incompletion_status`，并输出对应的 `*_text`、`*_scope` 及结构化 `incompletion_fields[]`。每条finding包含原始代码、SAP文本、对象、层级和双语说明。
