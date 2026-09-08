# 离线回归资料 / Offline regression reference

这是文档更新前、版本 0.2.0 的用法记录，供开发者回归使用，不代表当前网页运行入口或新的真机验收。命令的工作目录仍是 Agent 根目录；真实 SAP 调用只能在明确授权时执行。

These are pre-update instructions for version 0.2.0: developer regression material, not the current web workflow or new live acceptance. Commands still use the Agent root; live SAP access requires explicit authorization.

# GR/IR Clearing Assistant

SAPBusinessAgents 中的独立纵向切片：基于采购订单历史识别 GR/IR 未清原因、账龄、责任方与清理建议。所有源码、测试和样例均封装在 `agents/FI/gr-ir-clearing/` 内。

## 快速运行

无需 SAP 连接或第三方运行依赖：

```powershell
$env:PYTHONPATH = "src"
python -m grir_clearing --fixture fixtures/grir_sample.json --as-of 2026-07-22 --format json --output out/grir.json
python -m grir_clearing --fixture fixtures/grir_sample.json --as-of 2026-07-22 --format csv --output out/grir.csv
```

也可安装为命令：

```powershell
python -m pip install -e .
grir-clearing --fixture fixtures/grir_sample.json --month 2026-07 --output out/grir.json
```

运行测试：

```powershell
python -m pytest
```

详细设计、规则、SAP 字段映射和接入方式见 [GR/IR Clearing Assistant 说明](../docs/GRIR_CLEARING_ASSISTANT.md)。
