# Month-end Closing Assistant

这是一个严格只读的 SAP 月结异常检查纵向切片。输入公司代码、会计年度和期间后，它按配置执行 FI、CO、MM、SD 检查，输出异常、数据缺口、责任待办和三态关账结论：`可关账`、`不建议关账`、`存在阻塞项`。

## 数据通道

平台中的 Schema v2 Agent 只通过 Embedded SAP OData Provider 执行 GET，并使用确定性规则生成结果。Released API 证据不足时，平台可以条件调用 SAPSkillhub 中受信任的只读 Skill；证据仍不足时保持 `DATA_GAP` 或 `INCONCLUSIVE`。

包内 CLI 仅用于离线回归和人工复核证据：

- `fixture`：读取 scope-bound 示例或测试数据。
- `se16n`：读取人工触发、已经复核并绑定 SHA-256 的 SE16N manifest。

## 快速运行

在本目录执行：

```powershell
python -m pip install -e .
month-end-closing --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

使用经复核的 SE16N manifest：

```powershell
month-end-closing --gateway se16n `
  --se16n-manifest .local/runs/1010-2026-07/se16n-manifest.json `
  --sap-client 100 `
  --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

SE16N manifest 示例见 [config/se16n_manifest.example.json](config/se16n_manifest.example.json)。SAP client、公司代码、年度、期间、允许表、行数和文件哈希必须与清单一致；任一校验失败都会转为数据缺口，不会解释为零异常。

## 主要组件

- `agent.json`：平台可执行的 Embedded GET 与确定性分析步骤。
- `config/month_end_checklist.toml`：12 项检查、阈值、严重度和阻塞规则。
- `gateway.py`：只读数据端口、fixture adapter 和受控 fallback 组合器。
- `se16n_fallback.py`：复核 SE16N manifest、SAP client、scope 和 SHA-256。
- `engine.py`：检查编排、fail-closed 数据错误、异常分类和稳定待办编号。
- `models.py`：报告、异常、执行轨迹和待办的结构化模型。

## 安全边界

本助手不会调用 OB52、MMPV、AFAB、F.05、F.13、MR11 或任何写入、过账、开关期间和关账接口。平台 SAP 请求固定为 GET；人工 GUI 补证必须由操作人员显式触发。即使结论为 `可关账`，报告也固定声明 `closing_action_executed: false` 与 `closing_action_requires_human_confirmation: true`。

详细人工补证流程见 [docs/live-sap-runbook.md](docs/live-sap-runbook.md)。
