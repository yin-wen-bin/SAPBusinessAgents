# Order-to-Cash Status

从销售订单、客户PO、交货或发票出发，追踪到FI清账和回款状态。

## 能力

- 严格只读，Embedded SAP Read Provider优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：客户订单当前进行到哪一步，为什么还没有回款？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m order_to_cash_status "客户订单当前进行到哪一步，为什么还没有回款？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/order-to-cash-status/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

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
