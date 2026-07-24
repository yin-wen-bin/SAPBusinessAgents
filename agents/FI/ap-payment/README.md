# AP Payment Assistant

供应商付款状态查询与付款风险检查的可运行纵向切片。实现完全位于 `agents/FI/ap-payment/`，不依赖或改写其他 Agent。

支持的问题包括：

- “供应商 10001234 下周有哪些到期应付款？”
- “供应商 10001234 有哪些未清项目？”
- “发票 INV-PAID-001 付款了吗？”
- “检查供应商 10001234 的付款风险”

当前实现使用可替换的 JSON mock。业务逻辑不依赖具体 SAP SDK，接入真实 SAP 时只需实现 `SapApDataAdapter`。

## 快速运行

在 PowerShell 中：

```powershell
Set-Location D:\SAPBusinessAgents\agents\FI\ap-payment
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m ap_payment_assistant "供应商 10001234 下周有哪些到期应付款？" --as-of 2026-07-22
```

`--as-of` 用于让“本周/下周/未来 N 天”可重现；不传时使用系统当天。也可以安装为独立命令：

```powershell
python -m pip install -e .
ap-payment-assistant "发票 INV-PAID-001 付款了吗？" --as-of 2026-07-22
```

替换本地 fixture：

```powershell
python -m ap_payment_assistant "检查供应商 10004567 的付款风险" `
  --as-of 2026-07-22 `
  --fixture .\src\ap_payment_assistant\fixtures\mock_sap_ap.json
```

## 纵向链路

```text
自然语言
  -> ApIntentParser（意图、供应商/公司代码/发票/凭证/日期范围）
  -> ApPaymentAssistant（校验、默认值、查询编排）
  -> SapApDataAdapter（SAP 数据端口）
  -> PaymentRiskEngine（可解释规则及证据）
  -> AssistantResponse（摘要、项目、风险、回答、追踪）
```

目录：

```text
agents/FI/ap-payment/
├── docs/sap-adapter-contract.md
├── src/ap_payment_assistant/
│   ├── adapter.py
│   ├── intent.py
│   ├── mock_adapter.py
│   ├── models.py
│   ├── risk.py
│   ├── service.py
│   ├── cli.py
│   └── fixtures/mock_sap_ap.json
└── tests/
```

## 意图与参数

| 意图 | 典型表达 | 数据范围 |
|---|---|---|
| `upcoming_due` | 到期、应付款、下周、未来 N 天 | 未清项目，并按净到期日过滤 |
| `open_items` | 未清、未付款、欠款、余额 | 供应商全部未清项目 |
| `invoice_status` | 发票/会计凭证 + 付款状态 | 同时搜索未清和已清项目 |
| `payment_risk` | 风险、重复、冻结、异常银行、逾期 | 供应商全部未清项目 |

支持抽取供应商编号、公司代码、发票参考号、会计凭证号、财年以及明确日期范围。“下周”按下一自然周的周一至周日解析；仅说“到期”而未给时间范围时，默认查询未来 30 天，并在 `trace.extraction_notes` 中披露。

## 风险规则

| `rule_id` | 等级 | 判定 |
|---|---|---|
| `DUPLICATE_INVOICE_REFERENCE` | 高 | 同一供应商/公司代码的多个未清项目具有相同规范化发票参考号 |
| `DUPLICATE_AMOUNT` | 中 | 30 天内不同发票参考号出现相同供应商、币种和金额 |
| `PAYMENT_BLOCK` | 高 | 未清项目存在付款冻结代码 |
| `BANK_ACCOUNT_NOT_FOUND` | 高 | 项目引用的银行账户不在当前主数据快照中 |
| `ABNORMAL_BANK_ACCOUNT` | 高/中 | 银行账户未验证，或银行国家与供应商国家不一致 |
| `OVERDUE_PAYMENT` | 中 | 查询基准日已超过到期日且项目未清账 |

每项风险都包含关联凭证键、证据和建议动作。银行账户只输出掩码值。

## 结构化回答

CLI 返回 UTF-8 JSON，顶层字段为：

- `ok`、`errors`：执行状态和可操作的参数错误。
- `intent`、`parameters`：识别结果与最终使用的日期范围。
- `summary`：笔数、分币种金额、状态分布、风险等级分布。
- `items`：凭证、发票、金额、到期日、清账/付款运行和来源对象。
- `risks`：规则编号、等级、说明、关联凭证、证据和建议动作。
- `answer`：面向业务人员的简洁中文回答。
- `trace`：适配器健康状态、置信度、解析说明和实际来源对象。

进入 REGUH/REGUP 付款运行但尚未清账的项目返回 `scheduled`，不会误报为 `paid`；只有存在清账凭证/清账日期的项目返回 `paid`。

## SAP 接入

接口和字段映射详见 [SAP 数据适配契约](docs/sap-adapter-contract.md)。主要覆盖 FI-AP、MM-IV、Bank Accounting，并为 BSIK、BSAK、BKPF、BSEG、LFA1、LFB1、REGUH、REGUP 保留来源追踪；银行账户风险额外需要 LFBK 或等价的已批准 API。

生产适配器需要自行处理授权、分页、币种与借贷符号、付款条件净到期日、SAP 时区以及敏感字段掩码。mock 数据仅用于展示接口行为和规则测试，不代表真实 SAP 数据。

## 测试

```powershell
Set-Location D:\SAPBusinessAgents\agents\FI\ap-payment
python -m pytest
```

测试固定基准日为 `2026-07-22`，覆盖意图/参数抽取、mock 过滤、典型下周查询、已付款发票、重复发票/金额、付款冻结、异常银行账户、逾期付款和输入校验。
