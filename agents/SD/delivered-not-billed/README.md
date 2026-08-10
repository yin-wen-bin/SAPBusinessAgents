# Delivered-not-Billed Monitor

识别已完成PGI但尚未完全开票的外向交货，并按滞留时间分级。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
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

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
