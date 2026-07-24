# Month-end Closing Assistant

这是一个可运行的 SAP 月结异常检查纵向切片。输入公司代码、会计年度和期间后，它按配置执行 FI-AP、FI-AR、FI-GL、FI-AA、CO、MM、SD 检查，输出异常、责任待办和三态关账结论：`可关账`、`不建议关账`、`存在阻塞项`。

## Codex 项目级 Agent

工作区根目录包含项目级 Custom Agent：`.codex/agents/month_end_closing.toml`。从 `D:\SAPBusinessAgents` 打开 Codex 项目后，可显式调用：

```text
请使用 month_end_closing Agent 检查 2026 年 7 月公司代码 1710 的月结状态。
```

该 Agent 继承当前 Codex 会话的模型、推理强度、权限与 MCP 配置，不设置自动委派规则。它执行 SAPClaw_runtime MCP 优先、SAP GUI/SE16N 受控补证、现有 checklist/engine 判定和报告生成的完整流程，但不会执行任何过账或关账动作。

工作区已在 `D:\SAPBusinessAgents` 初始化 Git；应从该项目根目录打开 Codex。若直接把更深的子目录作为项目打开，根级 Agent 不保证被发现。

当前实现的数据源优先级是：SAPClaw_runtime MCP 导出、SAPClaw Thin Runtime 只读接口、经复核的 SAP GUI SE16N 导出，以及开发测试 fixture。MCP 对某项检查成功时不会读取 SE16N 文件；MCP 缺少服务、字段或完整结果时，才允许对该检查使用 scope-bound、SHA-256 校验且已记录复核人的 SE16N manifest。SE16N fallback 既支持标准 XLSX/CSV，也支持 `tools/run_se16n_grid_fallback.ps1` 生成的只读 ALV JSON；后者直接读取技术字段，避免 XXL/WPS 交接阻塞。所有来源最终标准化为 `CheckObservation`，决策、聚合与报告代码不随连接方式变化。

## 快速运行

在本目录执行：

```powershell
python -m pip install -e .
month-end-closing --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

使用已经启动的 SAPClaw Runtime：

```powershell
month-end-closing --gateway sapclaw `
  --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

直接处理由 Codex 的 SAPClaw_runtime MCP 工具导出的 bundle，并在单项缺数时使用 SE16N：

```powershell
month-end-closing --gateway mcp-export `
  --mcp-export .local/runs/1010-2026-07/mcp-export.json `
  --se16n-manifest .local/runs/1010-2026-07/se16n-manifest.json `
  --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

MCP bundle 示例见 [config/mcp_export.example.json](config/mcp_export.example.json)，SE16N manifest 示例见 [config/se16n_manifest.example.json](config/se16n_manifest.example.json)。只有 [config/sapclaw_queries.toml](config/sapclaw_queries.toml) 中标记为 `production_approved = true` 的 live Runtime 查询才会执行。这里的批准要求是 schema、服务端范围、分页、币种/空值/符号策略和业务异常定义已确认，不再要求与 FBL1N 等 GUI 报表做基线对账。

MCP bundle 和 SE16N manifest 都必须声明 SAP 客户端，并与 CLI 的 `--sap-client`（默认 `100`）一致；客户端不一致会在合并前直接失败。公司代码币种解析失败时，报告币种为 `UNRESOLVED`，不会回退到 checklist 示例币种。期间结束前运行的全绿结果最多只能得到“预关账快照”，不会被报告为最终可关账。

也可以使用结构化参数，并将报告写入指定文件：

```powershell
month-end-closing --company-code 1010 --year 2026 --period 7 `
  --config config/month_end_checklist.toml `
  --fixture fixtures/1010_2026_07.json `
  --output output/1010_2026_07.json
```

无需安装也可从源码运行：

```powershell
$env:PYTHONPATH = "src"
python -m sap_business_agents.month_end_closing --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

## 纵向切片

- `config/month_end_checklist.toml`：12 个配置化检查，覆盖指定模块、T-code 和核心表；每项定义阈值、严重度、阻塞性、责任部门、责任人、处理建议和人工确认要求。
- `checkers.py`：每个 SAP 模块有独立检查器注册点，共用可验证的阈值求值逻辑。
- `gateway.py`：SAP 只读访问接口、fixture adapter 和有审计轨迹的优先级 fallback。scope 不匹配、字段缺失或数据损坏均返回数据不可用。
- `sapclaw_runtime.py`：SAPClaw 本地 HTTP adapter、公司代码币种解析、K4 期间边界、完整分页、币种与最大行数保护。
- `mcp_export.py`：读取 scope-bound MCP 导出，要求只读声明、计划已校验、完整结果和可追踪 `case_id`。
- `se16n_fallback.py`：读取经复核的 SE16N manifest，验证 SAP system/client、scope、币种、允许表、文件存在性与 SHA-256。
- `config/sapclaw_queries.toml`：受控 GET 白名单和生产批准状态。
- `engine.py`：检查编排、fail-closed 数据错误、异常分类、稳定待办编号和关账结论。
- `models.py`：报告、异常、执行轨迹和待办的结构化模型。
- `fixtures/1010_2026_07.json`：可替换的示例 SAP 观测数据。

结论规则由 checklist 头部和每项检查共同控制：

1. 任一异常标记 `blocking = true`，或必需数据不可用：`存在阻塞项`。
2. 无阻塞项，但异常严重度达到 `not_recommended_at`：`不建议关账`。
3. 仅有低于该阈值的异常或没有异常：`可关账`。

报告同时按模块、责任部门、严重度汇总数量与金额。`finding_id` 和 `todo_id` 由公司代码、期间和检查 ID 稳定生成，便于外部工单系统幂等同步。

## 安全边界

本助手不会调用 OB52、MMPV、AFAB、F.05、F.13、MR11 或任何写入/过账接口。它只读取、判断并生成 `open` 待办；所有关账、开关期间、折旧、评估、清账、调整和重新过账动作都必须由授权人员在复核后确认执行。即使结论为 `可关账`，报告也固定声明 `closing_action_executed: false` 与 `closing_action_requires_human_confirmation: true`。

生产 adapter 还应落实：最小权限只读技术用户、公司代码/期间强制过滤、查询审计、金额与币种归一化、超时与重试边界，以及凭据不落盘。SAPClaw URL 被限制为本机 loopback，API key 仅从 `SAPCLAW_API_KEY` 环境变量读取。

SAP GUI Skills 不在后台 CLI 中静默运行。出现 MCP 数据缺口后，由操作人员明确触发 `sap-windowsgui-logon` 和 `sap-se16n-export`，使用新文件名完成受控导出；复核后生成带文件哈希的 manifest，再显式传给 `--se16n-manifest`。当前通用 SE16N Skill 只支持表名和最大命中数，不能安全地无筛选导出 BKPF/BSEG/COEP 等大表；此类对象必须先具备可靠的公司代码/期间选择条件，否则保持 `DATA_GAP`。详细流程见 [docs/live-sap-runbook.md](docs/live-sap-runbook.md)。系统绝不会用 mock 数据填补 live 失败项。

对于允许低命中数、随后人工复核范围的小型状态表，可显式运行 `tools/run_se16n_fallback.ps1`。该包装器依次调用登录和 SE16N Skills，仅允许 `T001`、`T001B`、`MARV`、`TABA`，拒绝覆盖已有文件，并返回文件 SHA-256。若本机 Excel/XXL 导出不可用，可使用 `tools/run_se16n_grid_fallback.ps1` 读取同一 SE16N ALV 网格并生成带系统、客户端、表名、行数与 SHA-256 的 JSON 证据。行项目大表不会通过这些无业务选择条件的包装器自动导出。

## 测试

```powershell
python -m pytest
```

测试覆盖样例阻塞报告、全绿状态、非阻塞重大异常、缺失 SAP 数据 fail-closed、中文典型问题解析、CLI JSON 输出、模块/T-code/表范围完整性、动态公司币种、MCP bundle、SE16N fallback 优先级、scope/允许表/哈希防篡改、查询批准门和期间边界。
