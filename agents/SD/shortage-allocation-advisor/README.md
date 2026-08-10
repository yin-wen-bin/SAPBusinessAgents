# Shortage Allocation Advisor

结合确认数量、物料库存和ATP，生成严格只读的短缺分配建议。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：库存不足时应该优先分配给哪些订单？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m shortage_allocation_advisor "库存不足时应该优先分配给哪些订单？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/shortage-allocation-advisor/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_SALES_ORDER_SRV`
- `API_MATERIAL_STOCK_SRV`
- `API_PRODUCT_AVAILY_INFO_BASIC`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
