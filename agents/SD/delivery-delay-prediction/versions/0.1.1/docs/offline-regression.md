# 离线回归资料 / Offline regression reference

这是文档更新前、版本 0.1.0 的用法记录，供开发者回归使用，不代表当前网页运行入口或新的真机验收。命令的工作目录仍是 Agent 根目录；真实 SAP 调用只能在明确授权时执行。

These are pre-update instructions for version 0.1.0: developer regression material, not the current web workflow or new live acceptance. Commands still use the Agent root; live SAP access requires explicit authorization.

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
