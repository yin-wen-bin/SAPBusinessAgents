# Delivery Delay Prediction

使用可解释规则对未完成交货生成0到100的延期风险评分。

## 能力

- 严格只读，Embedded SAP Read Provider优先，SAPSkillhub仅按缺口补证。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：未来两天哪些交货最可能延期？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m delivery_delay_prediction "未来两天哪些交货最可能延期？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local/runs/delivery-delay-prediction/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
