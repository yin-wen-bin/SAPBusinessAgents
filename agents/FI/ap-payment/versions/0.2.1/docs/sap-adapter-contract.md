# SAP 数据适配契约

`SapApDataAdapter` 是业务逻辑与 SAP 连接方式之间的唯一边界。mock、OData、RFC、CDS View 或 BTP Destination 实现均需提供：

- `search_payables(PayablesFilter)`：查询未清/已清供应商项目。
- `get_vendor_profile(vendor_id)`：读取供应商及公司代码层付款信息。
- `get_vendor_bank_accounts(vendor_id)`：只返回已授权用于风险检查的、经过掩码的银行数据。
- `health()`：返回适配器类型、连接状态及可审计的数据源标识。

## 领域字段与 SAP 语义

| 领域字段 | 典型 SAP 来源 | 说明 |
|---|---|---|
| `vendor_id` | BSIK/BSAK/BSEG-LIFNR | 供应商编号 |
| `company_code` | BSIK/BSAK/BKPF-BUKRS | 公司代码 |
| `accounting_document` / `fiscal_year` | BELNR / GJAHR | 与公司代码共同组成会计凭证键 |
| `invoice_reference` | BKPF-XBLNR | 外部发票参考号，重复发票规则的输入之一 |
| `amount` / `currency` | BSEG/索引视图金额与币种字段 | 适配器应统一借贷符号，保留 `Decimal` 精度 |
| `baseline_date` / `due_date` | ZFBDT、付款条件及净到期日计算 | 到期日必须按 SAP 付款条件规则计算或由发布 API 返回 |
| `payment_block` | BSEG-ZLSPR | 付款冻结 |
| `clearing_document` / `clearing_date` | AUGBL / AUGDT、BSAK | 判断已付款/清账状态 |
| `payment_run_id` | REGUH/REGUP | F110 建议或付款运行状态 |
| `VendorProfile` | LFA1/LFB1 | 一般及公司代码层供应商主数据 |
| `VendorBankAccount` | LFBK 或批准的供应商银行 API | 仅返回掩码账号；不要把明文账号写入日志或回答 |

S/4HANA Cloud 场景应优先使用已发布 CDS/OData API，不应把表名视为稳定的远程接口。`FBL1N`、`FK10N`、`FB03`、`F110`、`MIR4` 和 `ME23N` 用于业务人员复核，不是本适配器的技术协议。

## 生产实现约束

1. 在适配器内完成 SAP 授权校验、分页、币种/借贷符号和时区归一化。
2. 按 `BUKRS/GJAHR/BELNR/BUZEI` 保留凭证键，避免仅凭发票号误关联。
3. 对 REGUH/REGUP 区分“已进入付款建议/运行”和“已生成清账凭证”；前者不能回答为已付款。
4. 银行账户需返回稳定的内部 `account_id` 与掩码；验证状态应来自可信主数据流程。
5. `source_objects` 应反映实际读取来源，供结构化回答追踪，禁止硬编码成未读取的表。

