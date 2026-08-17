# Due Delivery Prioritization

按逾期、SAP交货优先级、临近程度、冻结和库存覆盖率排序交货需求。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：今天应该优先处理哪些到期交货？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m due_delivery_prioritization "今天应该优先处理哪些到期交货？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/due-delivery-prioritization/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`
- `API_MATERIAL_STOCK_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
