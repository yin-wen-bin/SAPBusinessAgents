# Delivered-not-Billed Monitor

以交货单项目为粒度识别完全未开票、部分开票和超量开票，计算剩余数量、预计未开票净额并按账龄分级。

## 能力

- 严格只读，Embedded SAP Read Provider优先，SAPSkillhub仅按缺口补证。
- 数量容差固定为 `0.001`；数量单位不一致时不换算、不猜测。
- 未开票净额按销售订单项目净额比例估算，明确标记为估算值；币种或来源证据不足时返回 `null`。
- `date_to` 同时作为PGI取样结束日、开票状态截止日和账龄截止日，默认当天且允许修改。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：哪些交货已经发货但还没有开票？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m delivered_not_billed "哪些交货已经发货但还没有开票？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/delivered-not-billed/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_OUTBOUND_DELIVERY_SRV`
- `API_BILLING_DOCUMENT_SRV`
- `API_SALES_ORDER_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
