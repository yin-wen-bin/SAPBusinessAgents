# delivered-not-billed 0.2.0 真机测试报告

- 测试日期：`2026-09-06T15:25:40+08:00`
- 代码基线：`bc63100+working-tree`
- 系统与客户端：已脱敏；连接配置和凭据不落库
- Embedded Provider：`embedded-odata` `2.0.0`，严格GET-only
- ADT：本流程未使用
- 安全边界：未执行任何SAP写操作。
- 技术状态：`completed`
- 业务结论：`通过`（当前样本）；状态覆盖结论：`部分通过`

## 真机证据

测试范围为一个已知有真实PGI记录的单日销售组织范围。输入值和原始业务行保存在被忽略的本地证据目录，仓库内不记录完整业务标识或金额。

| 步骤 | 实体 | 行数 | GET | 页数 | 查询源完整 |
| --- | --- | ---: | ---: | ---: | --- |
| `delivery_headers` | `API_OUTBOUND_DELIVERY_SRV/A_OutbDeliveryHeader` | 18 | 1 | 1 | true |
| `delivery_items` | `API_OUTBOUND_DELIVERY_SRV/A_OutbDeliveryItem` | 18 | 1 | 1 | true |
| `billing_items` | `API_BILLING_DOCUMENT_SRV/A_BillingDocumentItem` | 18 | 1 | 1 | true |
| `billing_headers` | `API_BILLING_DOCUMENT_SRV/A_BillingDocument` | 18 | 1 | 1 | true |
| `cancellation_documents_by_reference` | `API_BILLING_DOCUMENT_SRV/A_BillingDocument` | 0 | 0 | 0 | true（空绑定安全跳过） |
| `cancellation_documents_by_target` | `API_BILLING_DOCUMENT_SRV/A_BillingDocument` | 0 | 1 | 1 | true |
| `source_sales_items` | `API_SALES_ORDER_SRV/A_SalesOrderItem` | 18 | 1 | 1 | true |

实时Schema验证未发现缺失字段。本次全部业务查询均为GET，所有返回页完整且未截断。交货实体的实时元数据没有声明键字段可排序，因此未发送不受支持的`$orderby`；本次结果为单页，取得完整结果后再按`DeliveryDocument + DeliveryDocumentItem`确定性排序。

## Agent结果

| 指标 | 结果 |
| --- | ---: |
| `delivered_not_billed` | 0 |
| `unbilled_items` | 0 |
| `partially_billed_items` | 0 |
| `fully_billed_items` | 18 |
| `overbilled_items` | 0 |
| `inconclusive_items` | 0 |

固定Agent执行6次真实GET，输出18条项目级记录。直接Embedded基线与固定Agent规范化Hash均为`sha256:bd94ddd7e3250601d686be0a5be277500d874e38a2da2a838179e1dc2a98bc0a`，比较结果为`MATCH`。

## Fixture与状态覆盖

自动化测试覆盖完全未开票、部分开票、完全开票、超量开票、多张发票、截止日前后发票、取消关系、重复与冲突引用、单位/币种冲突、零负数量、缺少PGI日期、分页不完整、金额无法估算、空结果，以及7/8、30/31、60/61天账龄边界。

本次真机未观测到部分开票、取消和超量开票，因此这些状态只证明规则通过Fixture验证，不宣称真机状态通过。预计未开票净额仅在订单数量、单位、币种、取消链和分页均完整时生成，并明确标记`amount_is_estimate=true`；证据不足时返回`null`。

## Issue与复测

初次固定Agent预检发现新增关系未登记；补充4条精确只读关系并增加回归测试后复测通过。该问题属于本分支实现遗漏且已在同一修改中修复，未创建外部Issue。
