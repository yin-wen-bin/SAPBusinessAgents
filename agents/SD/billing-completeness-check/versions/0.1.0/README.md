# Billing Completeness Check

核对订单、交货和开票的数量、金额、币种、税码及重复引用。

## 能力

- 严格只读，Embedded SAP Read Provider优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：检查这张发票的数量、价格和税务是否完整。

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m billing_completeness_check "检查这张发票的数量、价格和税务是否完整。" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/billing-completeness-check/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`
- `API_BILLING_DOCUMENT_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
