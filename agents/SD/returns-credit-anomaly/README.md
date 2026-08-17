# Returns and Credit Anomaly Monitor

串联客户退货、贷项申请和后续凭证，识别超量、重复和引用异常。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：最近有哪些异常退货或贷项申请？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m returns_credit_anomaly "最近有哪些异常退货或贷项申请？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/returns-credit-anomaly/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_CUSTOMER_RETURN_SRV`
- `API_CREDIT_MEMO_REQUEST_SRV`
- `API_BILLING_DOCUMENT_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
