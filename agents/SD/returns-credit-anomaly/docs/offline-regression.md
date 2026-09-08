# 离线回归资料 / Offline regression reference

这是文档更新前、版本 0.1.0 的用法记录，供开发者回归使用，不代表当前网页运行入口或新的真机验收。命令的工作目录仍是 Agent 根目录；真实 SAP 调用只能在明确授权时执行。

These are pre-update instructions for version 0.1.0: developer regression material, not the current web workflow or new live acceptance. Commands still use the Agent root; live SAP access requires explicit authorization.

# Returns and Credit Anomaly Monitor

串联客户退货、贷项申请和后续凭证，识别超量、重复和引用异常。

## 能力

- 严格只读，Embedded SAP Read Provider优先，SAPSkillhub仅按缺口补证。
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
