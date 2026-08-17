# Billing Dispute Classification

将客户拒票与争议文本分类，并关联FI、POD及开票证据。

## 能力

- 严格只读，Thin SAPClaw优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：把最近的客户拒票按原因分类。

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m billing_dispute_classification "把最近的客户拒票按原因分类。" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/billing-dispute-classification/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_BILLING_DOCUMENT_SRV`
- `API_OPLACCTGDOCITEMCUBE_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
