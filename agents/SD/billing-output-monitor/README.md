# Billing Output Monitor

检查发票输出是否生成、发送和成功送达，并明确GUI补证缺口。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：哪些发票没有成功发送给客户？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m billing_output_monitor "哪些发票没有成功发送给客户？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/billing-output-monitor/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_BILLING_DOCUMENT_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
