# Order-to-Cash Anomaly Monitor

聚合订单、交货、开票和回款断点，形成统一异常待办。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：汇总最近订单到现金流程中最严重的异常。

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m order_to_cash_anomaly_monitor "汇总最近订单到现金流程中最严重的异常。" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/order-to-cash-anomaly-monitor/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`
- `API_BILLING_DOCUMENT_SRV`
- `API_OPLACCTGDOCITEMCUBE_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
