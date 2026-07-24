# Live SAP 数据获取与批准手册

## 能力分层

1. **SAPClaw Thin Runtime（首选）**：schema 驱动、只读、带查询审计；用于公司代码主数据、FI 行项目、试算平衡、物料凭证、采购历史和开票凭证等已索引服务。
2. **SAP Windows GUI 登录（前置能力）**：只负责创建并验证已认证会话。配置位于用户目录，程序只接收配置文件路径；不得读取或输出凭据。
3. **SE16N 导出（正式二级 fallback）**：当某项 MCP 检查缺少服务、字段或完整结果时，使用经复核、scope-bound、文件哈希校验的 SE16N manifest 补充。当前通用 Skill 不支持公司代码/期间筛选，禁止用无限命中数批量导出 BKPF、BSEG、COEP 等大表。
4. **SE38 源码导出（工程调查）**：用于查看已知、自有或已授权 ABAP extractor 的实现口径，不是月结运行时数据源。下载源码不代表允许执行该程序。

GUI 导出的 XLSX、ABAP 源码、截图与诊断文件放在 `.local/`，不会提交。后台 Assistant 不操纵桌面；只有经人工复核并写入 SE16N manifest、且文件 SHA-256 校验成功的数据，才可通过 `--se16n-manifest` 显式加入 fallback 链。

运行时必须锁定客户端。CLI 默认 `--sap-client 100`；MCP bundle 和 SE16N manifest 中的 `sap_client` 必须相同。`tools/run_se16n_fallback.ps1` 在调用通用 Skill 前要求系统中只有一个已认证、空闲且客户端匹配的 SAP GUI 会话，避免通用脚本误选其他客户端。

## SAPClaw 启动与检查

SAPClaw MCP 是 stdio transport，真实查询依赖本机 FastAPI backend。必须分别验证两者。后端入口：

```powershell
$env:PYTHONPATH = "D:\SAPClaw\src"
python -m uvicorn sap_odata_agent.api.app:create_app `
  --factory --host 127.0.0.1 --port 8000
```

然后检查 Thin Runtime health，应满足：backend 正常、`runtime_enabled = true`、`read_only = true`、SAP base URL 已配置。Assistant 的 HTTP client 只接受 `localhost`、`127.0.0.1` 或 `::1`，不会连接外部 Runtime 主机。

## MCP 查询从候选到批准

每个 checklist check 必须独立完成以下步骤：

1. 用 catalog 找候选服务。
2. 用 schema 确认 entity、字段类型、可筛选性和关系。
3. 编写最小 `$filter`、`$select` 与输出契约；公司代码和期间必须服务端过滤。
4. 用小范围真实数据验证分页、符号、空值、币种和证据主键。
5. 业务负责人确认异常定义、阈值、关键日期、去重键与空值策略。
6. 最后才把 `production_approved` 改为 `true`，并增加 schema 与业务规则测试。

不要求再用 FBL1N、FBL5N、FBL3N、MB5S 等 GUI 报表做基线验证。空到期日、冲销、正负配对、关键日期和币种仍必须采用明确的数据质量或业务规则；未明确的记录不能静默计入“无异常”。

## 建议来源矩阵

| Checklist 范围 | 首选数据源 | GUI/ABAP fallback | 批准重点 |
|---|---|---|---|
| FI-AP / FI-AR | Operational Accounting Document Item Cube | BSIK/BSAK、BSID/BSAD 受控导出 | 关键日期、账户类型、已清/未清、正负符号 |
| FI-GL | GL Account Line Item、Trial Balance | BSIS/BSAS、期间/运行状态受控导出 | 账套、期间、科目白名单、运行日志 |
| FI-AA | 经批准的固定资产 CDS/OData 或自有只读 extractor | AFAB 日志与 ANLC/ANEP 核验 | 折旧范围、测试/正式运行、错误状态 |
| MM | Purchase Order History、Material Document | EKBE 受控导出 | GR/IR 匹配、账龄、冲销和数量/金额单位 |
| CO | GL/CO 行项目服务或自有 extractor | COEP 受控导出 | 控制范围、成本要素、分配循环 |
| SD | Billing Document | 有限凭证与 FI 链接数据导出 | 会计传输状态、取消凭证、净额币种 |
| OB52 / MMPV | 自有只读状态 endpoint | 只读 GUI/ABAP 状态核验 | 只能读取；绝不由 Assistant 开关或结转期间 |

## GUI Skill 使用顺序

登录配置验证和登录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\sap-windowsgui-logon\scripts\logon.ps1" `
  -ValidateOnly

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$env:USERPROFILE\.codex\skills\sap-windowsgui-logon\scripts\logon.ps1"
```

SE16N 必须先用小表和低命中数测试。若出现外部 Windows“另存为”窗口，应停止并由经过验证的稳定控件适配器或人工完成，不能靠全局热键、屏幕坐标或标题猜测。

SE38 必须先下载已知的小程序验证，并使用新输出路径。保守模式建议关闭宽泛安全确认和持久安全决策：

```powershell
cscript //nologo `
  "$env:USERPROFILE\.codex\skills\sap-se38-export\scripts\se38_export.vbs" `
  /program:SAPLSE16N `
  /out:"<fresh-local-path>\SAPLSE16N.abap" `
  /trustallsecurity:false `
  /remembersecuritydecision:false
```

任何登录拒绝、多重登录、权限、覆盖、文件锁或未知多按钮对话框都必须停止，不能猜测处理。

对于小型期间状态表，可以由人员显式运行 Assistant 内的安全包装器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File tools\run_se16n_fallback.ps1 `
  -Table T001B `
  -File "T001B_1010_2026_07.xlsx" `
  -MaxHits 500
```

该脚本先验证登录配置，再登录、调用原始 SE16N Skill、拒绝覆盖并计算 SHA-256；只允许 `T001`、`T001B`、`MARV` 和 `TABA`。若 Excel/XXL 导出不可用，可改用 `tools/run_se16n_grid_fallback.ps1`，从同一 SE16N ALV 网格生成带系统、客户端、表名、列、行数与 SHA-256 的 JSON 证据。输出仍是 `review-required`，必须确认其中确实包含并正确归一化目标公司代码/期间。BSIK、BSEG、COEP 等大表不允许通过这些无业务选择条件的包装器导出。

## SE16N fallback 流程

1. MCP 对某项检查返回未映射、不可执行、不完整分页或缺少必需字段。
2. 记录该检查的 MCP 失败原因，不要重新解释为零异常。
3. 使用 `sap-windowsgui-logon` 先验证配置，再建立已认证会话；不得读取或输出配置内容。
4. 使用 `sap-se16n-export` 先完成低命中数验证，输出必须使用新文件名。
5. 只有在选择条件能可靠限定公司代码和期间时才能导出业务大表；否则停止并保留 `DATA_GAP`。
6. 财务复核导出范围、行数、金额、币种、空值和冲销后，填写 manifest。
7. 使用 PowerShell 计算文件哈希：

```powershell
Get-FileHash -Algorithm SHA256 ".local\se16n\BSIK_1010_2026_07.xlsx"
```

8. 运行 Assistant 时显式传入：

```powershell
month-end-closing --gateway mcp-export `
  --mcp-export .local\runs\1010-2026-07\mcp-export.json `
  --se16n-manifest .local\runs\1010-2026-07\se16n-manifest.json `
  --question "检查 2026 年 7 月公司代码 1010 的月结状态。"
```

MCP 成功的检查永远不会访问对应 SE16N 文件。MCP 失败而 SE16N 成功时，报告的 `source_mode` 是 `se16n_fallback`，并同时列出 MCP `unavailable` 记录和 SE16N `used` 记录。

## 经复核 SE16N manifest

GUI 或自有 ABAP 输出不得直接伪装成 SAPClaw 查询。复核后将其转换为与 fixture 相同的 scope-bound JSON：

```json
{
  "schema_version": "1.0",
  "source_type": "sap-se16n-export",
  "sap_system": "S4Q",
  "sap_client": "100",
  "reviewed_by": "Finance Reviewer",
  "reviewed_at": "2026-07-31T18:00:00+08:00",
  "scope": {
    "company_code": "1010",
    "fiscal_year": 2026,
    "period": 7
  },
  "currency": "EUR",
  "checks": {
    "AA_DEPRECIATION_PENDING": {
      "value": 0,
      "amount": "0.00",
      "currency": "EUR",
      "evidence": [],
      "data_quality_issues": [],
      "exports": [
        {
          "table": "ANLC",
          "file": "../se16n/ANLC_1010_2026_07.xlsx",
          "sha256": "<64-character-lowercase-sha256>",
          "row_count": 10,
          "selection_scope": {
            "company_code": "1010",
            "fiscal_year": 2026,
            "period": 7
          }
        }
      ]
    }
  }
}
```

scope、币种、表白名单、文件存在性、SHA-256 或复核元数据不符合要求时，Composite Gateway 会拒绝 fallback 并生成阻塞项。完整示例见 `config/se16n_manifest.example.json`。
