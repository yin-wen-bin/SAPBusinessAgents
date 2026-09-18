# SAP Business Agents

[中文](#中文) · [English](#english)

## 中文

### 产品用途与适用用户

`v0.1.0` 为 Windows 本地运行的预览版，采用 GitHub Release 源码安装。[在线中文目录](https://yin-wen-bin.github.io/SAPBusinessAgents/zh/)只能浏览能力说明；执行查询仍需安装并启动下方的本地服务。[版本说明与已知限制](docs/releases/v0.1.0.md) · [MIT 许可证](LICENSE) · [第三方资料署名](data/catalog-seed/NOTICE.md)。

SAP Business Agents 帮助财务、采购、销售、生产及业务支持人员，查询 SAP 业务状态、核对证据并形成可追踪的处理建议。固定 Agent 按已审核规则执行；自然语言功能负责理解和编排，平台管理的 SAP 调用仍接受只读与证据检查。SDK 原生命令的权限边界见下方安全说明。

SAP 访问限于 OData GET 或已批准的语义只读 ADT 查询。查询、催收建议和清账核对都不代表已经完成付款、催收或 SAP 过账。邮件发送是独立外部动作，必须逐次确认。

> 在线目录没有连接你的 SAP，也不保存你的运行记录；查询和 Agent 管理在本地服务中进行。

> 安全边界：自由查询和 Agent 对话编写使用 Codex SDK `full_access`，以启动服务的 Windows 账户权限运行，可执行命令、读写该账户可访问的文件并访问网络；工作副本不是操作系统沙盒。只在可信的本地账户和受控环境使用，不要把未知文档或网页内容视为可信指令。平台的 SAP Broker 仍限制其注册的 SAP 查询为只读，但不能从操作系统层面阻止 SDK 命令访问其他资源。详见[安全说明](SECURITY.md)。

### 按任务选择入口

| 你想做什么 | 选择哪个入口 | 是否需要 Agent Runtime |
|---|---|---|
| 重复检查应收、采购、库存或生产状态 | 按模块选择固定 Agent | 不需要；仍须验收与连接可用 |
| 临时提出没有固定入口的问题 | 自由查询 | 需要 |
| 把多个固定 Agent 串成流程 | 我的工作流 | 自然语言编写需要；确定性执行不需要 |
| 根据岗位职责寻找现有能力 | 岗位匹配助理 | 需要；不访问 SAP |
| 创建、修改、验证或停用固定 Agent | Agent 管理 | 对话编写需要；结构化管理不需要 |
| 配置工作流邮件连接 | 插件 | 需要可用的 Runtime App 或 HTTP MCP 连接；邮件发送须逐次确认 |

精选入口：[应收催收](agents/FI/ar-collection/README.md) · [银行来款核对](agents/FI/ar-cash-application/README.md) · [批量采购到付款](agents/MM/procure-to-pay-status/README.md) · [生产订单成本差异](agents/CO/product-cost-variance/README.md) · [MRP需求覆盖](agents/PP/demand-forecast-planning/README.md) · [新增销售需求模拟](agents/SD/new-sales-demand-coverage/README.md)。

### 首次安装与启动

Windows 本地运行；Python 最低 **3.11**，推荐与 CI 一致的 **3.13**；Node.js **22.13.0 或以上**（CI 使用 Node 22）。先安装 Git、Python、Node.js，并准备有只读权限的 SAP 连接。以下从 Release 标签取得源码并保留 Git 仓库；Agent 发布功能需要干净的本地 `main`，仅解压 GitHub 自动生成的源码 ZIP 不具备这一条件。

在 PowerShell 中：

```powershell
git clone --branch v0.1.0 https://github.com/yin-wen-bin/SAPBusinessAgents.git
cd SAPBusinessAgents
git switch -c main
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd site
npm ci
cd ..
Copy-Item .env.example .env
```

只在首次安装且不存在 `.env` 时执行最后一步。编辑本地 `.env`，填写只读 OData 连接的 `SAP_BASE_URL`、`SAP_USERNAME`、`SAP_PASSWORD` 和 `SAP_CLIENT`。不要提交凭据，也不要把密码写入问题文本。

#### 安装平台批准的 SAPSkillhub Skill

只运行内置 OData 功能时可以跳过本段。若希望本机识别当前全部已批准 Skill，在 **SAPBusinessAgents 仓库根目录**继续执行；SAPSkillhub 会放在仓库外的同级目录，不要将其复制进本站仓库：

```powershell
git clone --no-checkout https://github.com/yin-wen-bin/SAPSkillhub.git ..\SAPSkillhub
git -C ..\SAPSkillhub config core.autocrlf true
git -C ..\SAPSkillhub checkout --detach 6d6963749796bce7369a3b08420328c6addef01d
(Resolve-Path ..\SAPSkillhub).Path
```

此固定提交的两个 FI Skill 使用混合换行，Windows 全新检出会让批准摘要不匹配。在**刚克隆、无本地修改**的 SAPSkillhub 副本上执行以下纯格式准备；命令先核对提交与工作区，最后检查 Git 语义内容没有变化。不要在已有修改的副本上执行：

```powershell
$skillhubRoot = (Resolve-Path ..\SAPSkillhub).Path
if ((git -C $skillhubRoot rev-parse HEAD) -ne '6d6963749796bce7369a3b08420328c6addef01d') { throw 'Wrong SAPSkillhub commit' }
if (@(git -C $skillhubRoot status --porcelain --untracked-files=no).Count) { throw 'SAPSkillhub has local changes' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$lfFiles = @(git -C $skillhubRoot ls-files -- 'skills/FI/sap-bank-receipt-evidence') + @(
  'skills/FI/sap-ar-dunning-history-evidence/README.en.md',
  'skills/FI/sap-ar-dunning-history-evidence/README.zh-CN.md',
  'skills/FI/sap-ar-dunning-history-evidence/references/public-output.schema.json',
  'skills/FI/sap-ar-dunning-history-evidence/references/restricted-row.schema.json'
)
foreach ($relative in $lfFiles) {
  $path = Join-Path $skillhubRoot $relative
  $value = [IO.File]::ReadAllText($path, $utf8).Replace("`r`n", "`n")
  [IO.File]::WriteAllText($path, $value, $utf8)
}
$manifestPath = Join-Path $skillhubRoot 'skills/FI/sap-ar-dunning-history-evidence/manifest.json'
$manifest = [IO.File]::ReadAllText($manifestPath, $utf8).Replace("`r`n", "`n").Replace("`n", "`r`n")
foreach ($field in @('output_schema', 'public_output_schema', 'restricted_row_schema')) {
  $pattern = '(?m)^([^\r\n]*"' + $field + '"[^\r\n]*)\r\n'
  if ([regex]::Matches($manifest, $pattern).Count -ne 1) { throw "Missing manifest field: $field" }
  $manifest = [regex]::Replace($manifest, $pattern, ('$1' + "`n"))
}
[IO.File]::WriteAllText($manifestPath, $manifest, $utf8)
git -c core.safecrlf=false -C $skillhubRoot diff --quiet -- skills/FI/sap-ar-dunning-history-evidence skills/FI/sap-bank-receipt-evidence
if ($LASTEXITCODE) { throw 'Unexpected Skillhub content change' }
git -c core.safecrlf=false -C $skillhubRoot add -u -- skills/FI/sap-ar-dunning-history-evidence skills/FI/sap-bank-receipt-evidence
if ($LASTEXITCODE) { throw 'Could not refresh Skillhub index' }
if (@(git -C $skillhubRoot status --porcelain --untracked-files=no).Count) { throw 'Skillhub checkout is not clean' }
```

这一步只还原批准快照所需的换行字节，不访问 SAP、不放宽批准规则。将上面输出的**绝对路径**填入 SAPBusinessAgents 的 `.env`，例如 `SAPSKILLHUB_ROOT=C:/work/SAPSkillhub`；使用自己的实际路径，不保留示例值或空值。Skill 由 SAPBusinessAgents 的 Python 虚拟环境启动；平台已依赖 `requests==2.34.2`，无需另装 SAPSkillhub 网站的 Node 依赖，也无需逐个安装到 Codex App。

平台当前批准以下 5 个 Skill；配置细节以固定提交中的 Skill 文档为准：

| Skill | 用途与配置说明 |
|---|---|
| [`sap-adt-table-export`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/Common/sap-adt-table-export/README.zh-CN.md) | 在明确证据缺口下读取有界 ADT 表/CDS 证据；需 Skill 自有连接配置和仓库外的默认 Profile。 |
| [`sap-ar-dunning-history-evidence`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/FI/sap-ar-dunning-history-evidence/README.zh-CN.md) | 读取已执行的历史催收事件；需目标系统认可的固定来源 Profile。 |
| [`sap-bank-receipt-evidence`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/FI/sap-bank-receipt-evidence/README.zh-CN.md) | 读取银行来款、冲销及处理证据；还需 Skill 自有的账号 HMAC 密钥。 |
| [`sap-production-order-cost-analysis`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/CO/sap-production-order-cost-analysis/README.zh-CN.md) | 读取生产订单成本证据；需目标系统的只读成本来源。 |
| [`sap-wbs-object-resolver`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/CO/sap-wbs-object-resolver/README.zh-CN.md) | 将 WBS 外部编号解析为控制对象；需目标系统认可的固定来源 Profile。 |

OData 连接和 SAPSkillhub 的 ADT 连接是**两套独立配置**。从仓库根目录复制 [ADT Skill 的 `.env.example`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/Common/sap-adt-table-export/.env.example)和[示例 Profile](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/Common/sap-adt-table-export/references/profiles.example.json)；以下命令不会覆盖已有本地配置：

```powershell
$skillhubRoot = (Resolve-Path ..\SAPSkillhub).Path
$adtSkillDir = Join-Path $skillhubRoot 'skills\Common\sap-adt-table-export'
$profileDir = Join-Path $env:LOCALAPPDATA 'SAPBusinessAgents\sap-adt'
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
$profileFile = Join-Path $profileDir 'profiles.json'
if (-not (Test-Path (Join-Path $adtSkillDir '.env'))) { Copy-Item (Join-Path $adtSkillDir '.env.example') (Join-Path $adtSkillDir '.env') }
if (-not (Test-Path $profileFile)) { Copy-Item (Join-Path $adtSkillDir 'references\profiles.example.json') $profileFile }
$profileFile
```

编辑 Skill 目录中被 Git 忽略的 `.env`，填写专用的只读 `SAP_ADT_*` 连接值，保持 `SAP_ADT_VERIFY_SSL=true`，并将 `SAP_ADT_PROFILES_FILE` 改为上面输出的**绝对路径**。编辑仓库外的 `profiles.json`，核对 `default_profile`、系统 ID、来源和权限；示例中的系统、账号和 `supported` 标志并不证明你的 SAP 已通过验证。使用银行来款 Skill 时，还须在其目录被 Git 忽略的 `.env` 中设置 `SAP_BANK_RECEIPT_HMAC_KEY_ID=bank-receipt-hmac-v1` 和由至少 32 个随机字节生成的 Base64 `SAP_BANK_RECEIPT_HMAC_KEY_B64`（不要复用示例或公开密钥）。密钥、Profile 和连接凭据都不得进入 Git。其他固定来源 Skill 也须按照上表各自文档核对目标系统；不要把维护者系统的来源结论直接套用到新系统。

在仓库根目录运行以下**不访问 SAP**的依赖检查；Python 必须是刚安装 SAPBusinessAgents 的虚拟环境：

```powershell
$skillCheckPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
Push-Location ..\SAPSkillhub\skills\Common\sap-adt-table-export
try { & $skillCheckPython .\scripts\check_environment.py } finally { Pop-Location }
```

`sap-control-object-commitment-evidence`虽在平台清单中，但当前 `validated=false`，不会进入批准目录；安装文件不能使它获得执行资格。Skill 包可用也不等于目标 SAP 权限、Profile、对象和业务证据已验证；缺少依赖 Skill 时，引用它的固定 Agent 会拒绝运行。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-SAPBusinessAgents.ps1
```

打开 [本地中文主页](http://127.0.0.1:4321/zh/)；本地 API 默认监听 `127.0.0.1:8765`。自然语言功能还需在“系统配置 → Agent Runtime与SDK”确认登录、刷新模型目录、检查兼容性、选择默认模型、启用并选择默认 Runtime。可选模型以当前 SDK 和账号返回的完整目录为准，不使用固定型号清单。

启动后核对已批准 Skill（只读取本地 API，不访问 SAP）；缺任何一个 ID 都应先检查 `SAPSKILLHUB_ROOT`、固定提交、Schema 和 Skill 依赖，不要仅凭目录存在或 API 健康就开始依赖它的 Agent：

```powershell
$expectedSkillIds = @('sap-adt-table-export', 'sap-ar-dunning-history-evidence', 'sap-bank-receipt-evidence', 'sap-production-order-cost-analysis', 'sap-wbs-object-resolver')
$approvedSkills = Invoke-RestMethod http://127.0.0.1:8765/api/tools/skills
$missingSkillIds = @($expectedSkillIds | Where-Object { $_ -notin $approvedSkills.skill_id })
if ($missingSkillIds.Count) { throw "Missing approved Skills: $($missingSkillIds -join ', ')" }
$approvedSkills | Select-Object skill_id
```

安装细节、端口和常见故障见 [首次使用指南](docs/getting-started.md)，模型与 SDK 更新见 [Runtime 设置](docs/runtime-settings.md)。

### 第一次运行与结果解读

1. 选择一个已启用且验收通过的 Agent，阅读它的适用范围和证据限制。
2. 输入本系统有权限查看的业务编号、组织范围及日期。文档场景仅是示例，不保证存在对应 SAP 数据。
3. 运行后先看业务结论、缺口和建议动作，再展开凭证与明细。
4. 区分“读取完整”“业务证据充分”和“业务完成”。`inconclusive` 表示不能确认，不是业务成功；完整零结果不同于查询失败。
5. 如需查看受限明细，主动揭示并使用受保护的下载入口；不要把原始银行参考号写入自由文本。

FI 清账不自动证明银行到账；MRP模拟不等于正式ATP；处理建议不等于已执行处理。

### 功能与完整目录

[自由查询](docs/free-query.md) · [Agent 管理](docs/agent-lifecycle-management.md) · [我的工作流](docs/user-workflows.md) · [岗位匹配](docs/role-agent-matching.md) · [AR 业务指南](docs/ar-collection-cash-application.md) · [插件与连接](docs/runtime-integrations.md)。

下表由当前清单及生命周期记录生成；“使用中”与“通过验收”是不同条件。停用或受阻条目保留说明，不意味着可以运行。

<!-- generated:agents-zh:start -->
| 模块 | Agent | 版本 | 生命周期 | 验收 |
|---|---|---|---|---|
| CO | [预算滚动预测助手](agents/CO/budget-rolling-forecast/README.md) | 0.1.1 | 使用中 | 因证据或验收缺口受阻 |
| CO | [CO 月结分配与结算助手](agents/CO/co-month-end-allocation-settlement/README.md) | 0.1.1 | 使用中 | 因证据或验收缺口受阻 |
| CO | [成本中心费用异常助手](agents/CO/cost-center-expense-anomaly/README.md) | 0.1.1 | 使用中 | 因证据或验收缺口受阻 |
| CO | [内部订单与项目控制助手](agents/CO/internal-order-project-control/README.md) | 0.4.0 | 使用中 | 因证据或验收缺口受阻 |
| CO | [生产订单成本差异分析助手](agents/CO/product-cost-variance/README.md) | 0.2.0 | 使用中 | 验收通过 |
| Common | [岗位匹配助理](agents/Common/role-agent-matching/README.md) | 0.2.0 | 使用中 | 平台能力门禁 |
| FI | [应付账款付款助手](agents/FI/ap-payment/README.md) | 0.2.1 | 使用中 | 验收通过 |
| FI | [银行来款与应收核销核对助手](agents/FI/ar-cash-application/README.md) | 0.1.0 | 使用中 | 验收通过 |
| FI | [应收账款催收助手](agents/FI/ar-collection/README.md) | 1.2.0 | 使用中 | 验收通过 |
| FI | [GR/IR 清账助手](agents/FI/gr-ir-clearing/README.md) | 0.2.1 | 使用中 | 验收通过 |
| FI | [月结助手](agents/FI/month-end-closing/README.md) | 0.2.1 | 使用中 | 验收通过 |
| MM | [智能寻源与 RFQ 评估助手](agents/MM/intelligent-sourcing-rfq/README.md) | 0.1.1 | 使用中 | 验收通过 |
| MM | [库存健康检查](agents/MM/inventory-health-balancing/README.md) | 0.4.0 | 使用中 | 验收通过 |
| MM | [外购件短缺采购响应助手](agents/MM/material-shortage-procurement-response/README.md) | 0.2.1 | 使用中 | 验收通过 |
| MM | [采购订单入库状态检查](agents/Common/mm-po-gr-status/README.md) | 0.1.1 | 使用中 | 验收通过 |
| MM | [采购到付款状态助手](agents/MM/procure-to-pay-status/README.md) | 0.3.2 | 使用中 | 验收通过 |
| MM | [供应商绩效与交付风险助手](agents/MM/supplier-performance-risk/README.md) | 0.2.3 | 使用中 | 验收通过 |
| PP | [计划订单与计划独立需求覆盖度助手](agents/PP/demand-forecast-planning/README.md) | 0.3.1 | 使用中 | 验收通过 |
| PP | [MRP 异常分析助手](agents/PP/mrp-exception-analysis/README.md) | 0.2.1 | 使用中 | 验收通过 |
| PP | [生产订单执行监控助手](agents/PP/production-order-monitoring/README.md) | 0.2.1 | 使用中 | 验收通过 |
| PP | [生产排程与产能助手](agents/PP/production-scheduling-capacity/README.md) | 0.1.0 | 使用中 | 因证据或验收缺口受阻 |
| PP | [生产数量与物料差异根因分析助手](agents/PP/production-variance-analysis/README.md) | 0.2.1 | 使用中 | 验收通过 |
| SD | [开票冻结诊断](agents/SD/billing-block-diagnosis/README.md) | 0.2.1 | 使用中 | 验收通过 |
| SD | [发票完整性检查](agents/SD/billing-completeness-check/README.md) | 0.1.1 | 使用中 | 验收通过 |
| SD | [发票争议分类](agents/SD/billing-dispute-classification/README.md) | 0.1.0 | 已停用 | 因证据或验收缺口受阻 |
| SD | [发票输出监控](agents/SD/billing-output-monitor/README.md) | 0.1.0 | 已停用 | 因证据或验收缺口受阻 |
| SD | [已发货未开票监控](agents/SD/delivered-not-billed/README.md) | 0.2.1 | 使用中 | 验收通过 |
| SD | [交货延期预测](agents/SD/delivery-delay-prediction/README.md) | 0.1.1 | 使用中 | 验收通过 |
| SD | [到期交货优先级排序](agents/SD/due-delivery-prioritization/README.md) | 0.1.1 | 使用中 | 验收通过 |
| SD | [新增销售需求覆盖度检查助手](agents/SD/new-sales-demand-coverage/README.md) | 0.1.0 | 使用中 | 验收通过 |
| SD | [订单到收款状态助手](agents/SD/order-to-cash-status/README.md) | 0.1.2 | 使用中 | 验收通过 |
| SD | [退货及贷项异常监控](agents/SD/returns-credit-anomaly/README.md) | 0.1.0 | 使用中 | 因证据或验收缺口受阻 |
| SD | [缺货分配建议](agents/SD/shortage-allocation-advisor/README.md) | 0.1.0 | 使用中 | 因证据或验收缺口受阻 |
<!-- generated:agents-zh:end -->

### 开发、测试与部署

[开发指南](docs/developer-guide.md) 收录架构、Schema、接口、离线回归、目录校验、贡献及静态部署说明。历史验收报告按原日期和适用版本保留，不代表重新验证了当前环境。

## English

### Purpose and audience

`v0.1.0` is a Windows-local preview installed from GitHub Release source. The [online English catalog](https://yin-wen-bin.github.io/SAPBusinessAgents/en/) is for browsing only; queries require installing and starting the local service below. See the [release notes and known limitations](docs/releases/v0.1.0.md), [MIT license](LICENSE) and [third-party attribution](data/catalog-seed/NOTICE.md).

SAP Business Agents helps finance, procurement, sales, production and support teams inspect SAP status, reconcile evidence and produce traceable follow-up advice. Fixed Agents run reviewed deterministic rules; natural-language features interpret and compose requests, while platform-managed SAP calls remain subject to read-only and evidence checks. The SDK's native command permissions are described in the security note below.

SAP access is limited to OData GET or approved semantically read-only ADT queries. Query results, collection advice and reconciliation do not execute payments, dunning or SAP postings. Email sending is a separate external action requiring confirmation each time.

> The online catalog is not connected to your SAP system and does not store your runs; queries and Agent management use the local service.

> Security boundary: free queries and conversational Agent authoring use the Codex SDK in `full_access` under the Windows account that starts the service. It can run commands, read or write files accessible to that account, and use the network; a work copy is not an OS sandbox. Use a trusted local account and environment, and treat external content as untrusted data. The platform SAP Broker limits its registered SAP queries to read-only operations, but cannot prevent SDK commands from accessing other resources at the OS level. See [Security](SECURITY.md).

### Choose by task

| Your task | Entry point | Agent Runtime required? |
|---|---|---|
| Repeat an AR, purchasing, inventory or production check | A fixed Agent in its module | No; acceptance and connections still apply |
| Ask an ad-hoc question without a fixed entry | Free query | Yes |
| Combine fixed Agents into a process | My workflows | For natural-language authoring; not deterministic execution |
| Match job responsibilities to capabilities | Role matching assistant | Yes; it does not access SAP |
| Create, edit, validate or deactivate fixed Agents | Agent management | For conversational authoring; not structured management |
| Configure workflow mail connections | Plugins | Requires a ready Runtime App or HTTP MCP connection; each mail send requires confirmation |

Selected tasks: [AR collection](agents/FI/ar-collection/README.md), [bank reconciliation](agents/FI/ar-cash-application/README.md), [batch P2P](agents/MM/procure-to-pay-status/README.md), [production-order costs](agents/CO/product-cost-variance/README.md), [MRP demand coverage](agents/PP/demand-forecast-planning/README.md), [new sales-demand simulation](agents/SD/new-sales-demand-coverage/README.md).

### First installation and startup

Use Windows locally. Python **3.11** is the minimum; **3.13** is recommended to match CI. Node.js must be **22.13.0 or newer** (CI uses Node 22). Install Git, Python and Node.js, and obtain a read-only SAP connection. The commands below obtain the Release tag while retaining a Git repository. Agent publication requires a clean local `main`; extracting GitHub's automatic source ZIP alone does not provide one.

In PowerShell:

```powershell
git clone --branch v0.1.0 https://github.com/yin-wen-bin/SAPBusinessAgents.git
cd SAPBusinessAgents
git switch -c main
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd site
npm ci
cd ..
Copy-Item .env.example .env
```

Run the final command only on first installation when `.env` does not exist. Edit the local `.env` with the read-only OData connection values `SAP_BASE_URL`, `SAP_USERNAME`, `SAP_PASSWORD` and `SAP_CLIENT`. Never commit credentials or put passwords in questions.

#### Install platform-approved SAPSkillhub Skills

Skip this section if you only use built-in OData capabilities. To make all currently approved Skills discoverable, continue **from the SAPBusinessAgents repository root**. Keep SAPSkillhub in a sibling directory, outside this repository:

```powershell
git clone --no-checkout https://github.com/yin-wen-bin/SAPSkillhub.git ..\SAPSkillhub
git -C ..\SAPSkillhub config core.autocrlf true
git -C ..\SAPSkillhub checkout --detach 6d6963749796bce7369a3b08420328c6addef01d
(Resolve-Path ..\SAPSkillhub).Path
```

Two FI Skills in this pinned commit have mixed line endings; a fresh Windows checkout otherwise fails their approved digests. Run the following format-only preparation on the **new, clean** SAPSkillhub checkout. It checks the commit and Git state first, and verifies that Git content remains unchanged. Do not run it over your own edits:

```powershell
$skillhubRoot = (Resolve-Path ..\SAPSkillhub).Path
if ((git -C $skillhubRoot rev-parse HEAD) -ne '6d6963749796bce7369a3b08420328c6addef01d') { throw 'Wrong SAPSkillhub commit' }
if (@(git -C $skillhubRoot status --porcelain --untracked-files=no).Count) { throw 'SAPSkillhub has local changes' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$lfFiles = @(git -C $skillhubRoot ls-files -- 'skills/FI/sap-bank-receipt-evidence') + @(
  'skills/FI/sap-ar-dunning-history-evidence/README.en.md',
  'skills/FI/sap-ar-dunning-history-evidence/README.zh-CN.md',
  'skills/FI/sap-ar-dunning-history-evidence/references/public-output.schema.json',
  'skills/FI/sap-ar-dunning-history-evidence/references/restricted-row.schema.json'
)
foreach ($relative in $lfFiles) {
  $path = Join-Path $skillhubRoot $relative
  $value = [IO.File]::ReadAllText($path, $utf8).Replace("`r`n", "`n")
  [IO.File]::WriteAllText($path, $value, $utf8)
}
$manifestPath = Join-Path $skillhubRoot 'skills/FI/sap-ar-dunning-history-evidence/manifest.json'
$manifest = [IO.File]::ReadAllText($manifestPath, $utf8).Replace("`r`n", "`n").Replace("`n", "`r`n")
foreach ($field in @('output_schema', 'public_output_schema', 'restricted_row_schema')) {
  $pattern = '(?m)^([^\r\n]*"' + $field + '"[^\r\n]*)\r\n'
  if ([regex]::Matches($manifest, $pattern).Count -ne 1) { throw "Missing manifest field: $field" }
  $manifest = [regex]::Replace($manifest, $pattern, ('$1' + "`n"))
}
[IO.File]::WriteAllText($manifestPath, $manifest, $utf8)
git -c core.safecrlf=false -C $skillhubRoot diff --quiet -- skills/FI/sap-ar-dunning-history-evidence skills/FI/sap-bank-receipt-evidence
if ($LASTEXITCODE) { throw 'Unexpected Skillhub content change' }
git -c core.safecrlf=false -C $skillhubRoot add -u -- skills/FI/sap-ar-dunning-history-evidence skills/FI/sap-bank-receipt-evidence
if ($LASTEXITCODE) { throw 'Could not refresh Skillhub index' }
if (@(git -C $skillhubRoot status --porcelain --untracked-files=no).Count) { throw 'Skillhub checkout is not clean' }
```

This only restores the approved snapshot's line-ending bytes; it does not access SAP or relax approval. Set `SAPSKILLHUB_ROOT` in the SAPBusinessAgents `.env` to the **absolute path** printed above, for example `SAPSKILLHUB_ROOT=C:/work/SAPSkillhub`; replace the example with your own path rather than leaving it blank. SAPBusinessAgents starts Skills with its own Python virtual environment and already depends on `requests==2.34.2`. Do not install SAPSkillhub's website Node dependencies or install each Skill separately into Codex App.

The platform currently approves these five Skills; use the linked documentation from the pinned commit for target-specific configuration:

| Skill | Purpose and configuration |
|---|---|
| [`sap-adt-table-export`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/Common/sap-adt-table-export/README.en.md) | Bounded ADT table/CDS evidence for a confirmed gap; requires Skill-owned connection settings and an external default profile. |
| [`sap-ar-dunning-history-evidence`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/FI/sap-ar-dunning-history-evidence/README.en.md) | Executed historical dunning events; requires a source profile validated for the target SAP system. |
| [`sap-bank-receipt-evidence`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/FI/sap-bank-receipt-evidence/README.en.md) | Bank receipt, reversal and processing evidence; also requires a Skill-owned account HMAC key. |
| [`sap-production-order-cost-analysis`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/CO/sap-production-order-cost-analysis/README.en.md) | Production-order cost evidence; requires read-only cost sources on the target SAP system. |
| [`sap-wbs-object-resolver`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/CO/sap-wbs-object-resolver/README.en.md) | Resolves an external WBS ID to a controlling object; requires a validated source profile. |

The SAPBusinessAgents OData connection and SAPSkillhub ADT connection are **separate**. From the repository root, copy the [ADT Skill `.env.example`](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/Common/sap-adt-table-export/.env.example) and [profile example](https://github.com/yin-wen-bin/SAPSkillhub/blob/6d6963749796bce7369a3b08420328c6addef01d/skills/Common/sap-adt-table-export/references/profiles.example.json). These commands do not overwrite existing local settings:

```powershell
$skillhubRoot = (Resolve-Path ..\SAPSkillhub).Path
$adtSkillDir = Join-Path $skillhubRoot 'skills\Common\sap-adt-table-export'
$profileDir = Join-Path $env:LOCALAPPDATA 'SAPBusinessAgents\sap-adt'
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
$profileFile = Join-Path $profileDir 'profiles.json'
if (-not (Test-Path (Join-Path $adtSkillDir '.env'))) { Copy-Item (Join-Path $adtSkillDir '.env.example') (Join-Path $adtSkillDir '.env') }
if (-not (Test-Path $profileFile)) { Copy-Item (Join-Path $adtSkillDir 'references\profiles.example.json') $profileFile }
$profileFile
```

Edit the ignored `.env` in the Skill directory with its own read-only `SAP_ADT_*` connection values, keep `SAP_ADT_VERIFY_SSL=true`, and set `SAP_ADT_PROFILES_FILE` to the **absolute path** printed above. Edit `profiles.json` outside both repositories to verify its `default_profile`, system ID, sources and permissions. The example system, account and `supported` value are not evidence that your SAP system has been validated. For bank receipts, also set `SAP_BANK_RECEIPT_HMAC_KEY_ID=bank-receipt-hmac-v1` and `SAP_BANK_RECEIPT_HMAC_KEY_B64` (Base64 of at least 32 random bytes; do not reuse an example or public key) in that Skill's own ignored `.env`. Never commit keys, profiles or connection credentials. Check each other fixed-source Skill against its linked documentation rather than assuming the maintainer's source validation transfers to another SAP system.

From the repository root, run this dependency check **without accessing SAP**, using the SAPBusinessAgents virtual environment:

```powershell
$skillCheckPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
Push-Location ..\SAPSkillhub\skills\Common\sap-adt-table-export
try { & $skillCheckPython .\scripts\check_environment.py } finally { Pop-Location }
```

`sap-control-object-commitment-evidence` is listed but remains `validated=false` and is excluded from the approved catalog; installing its files cannot make it executable. A discoverable package does not prove the target SAP permissions, profiles, objects or business evidence. A fixed Agent referencing a missing Skill is rejected before execution.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-SAPBusinessAgents.ps1
```

Open the [local English home](http://127.0.0.1:4321/en/); the API listens on `127.0.0.1:8765` by default. For natural-language features, use System settings → Agent Runtime and SDK to confirm login, refresh the model catalog, check compatibility, choose a model, enable the Runtime and select the default Runtime. Model choices come from the installed SDK and account, not a fixed list.

After startup, verify the approved Skills through the local API; this check **does not query SAP**. If any ID is missing, inspect `SAPSKILLHUB_ROOT`, the pinned checkout, schemas and dependencies before using an Agent that needs it. A directory or healthy API alone is insufficient:

```powershell
$expectedSkillIds = @('sap-adt-table-export', 'sap-ar-dunning-history-evidence', 'sap-bank-receipt-evidence', 'sap-production-order-cost-analysis', 'sap-wbs-object-resolver')
$approvedSkills = Invoke-RestMethod http://127.0.0.1:8765/api/tools/skills
$missingSkillIds = @($expectedSkillIds | Where-Object { $_ -notin $approvedSkills.skill_id })
if ($missingSkillIds.Count) { throw "Missing approved Skills: $($missingSkillIds -join ', ')" }
$approvedSkills | Select-Object skill_id
```

See [Getting started](docs/getting-started.md) for installation, ports and troubleshooting, and [Runtime settings](docs/runtime-settings.md) for models and SDK updates.

### First run and interpreting results

1. Select an active Agent with passed acceptance, then read its scope and evidence limitations.
2. Supply permitted business identifiers, organizational scope and dates. Documentation examples do not guarantee data exists in SAP.
3. Read the business conclusion, gaps and actions before opening document detail.
4. Distinguish complete source reads, sufficient business evidence and completed business processes. `inconclusive` is not success; complete empty results differ from failed queries.
5. Explicitly reveal protected details and use guarded downloads. Do not put raw bank references in free text.

FI clearing does not prove bank settlement; MRP simulation is not formal ATP; advice is not an executed action.

### Guides and full catalog

[Free query](docs/free-query.md) · [Agent management](docs/agent-lifecycle-management.md) · [My workflows](docs/user-workflows.md) · [Role matching](docs/role-agent-matching.md) · [AR guide](docs/ar-collection-cash-application.md) · [Plugins and connections](docs/runtime-integrations.md).

This index is generated from current manifests and lifecycle records. Active lifecycle and passed acceptance are separate requirements. Inactive or blocked entries remain documented but are not necessarily runnable.

<!-- generated:agents-en:start -->
| Module | Agent | Version | Lifecycle | Acceptance |
|---|---|---|---|---|
| CO | [Budget Rolling Forecast Assistant](agents/CO/budget-rolling-forecast/README.md) | 0.1.1 | Active | Blocked |
| CO | [CO Month-End Allocation and Settlement Assistant](agents/CO/co-month-end-allocation-settlement/README.md) | 0.1.1 | Active | Blocked |
| CO | [Cost Center Expense Anomaly Assistant](agents/CO/cost-center-expense-anomaly/README.md) | 0.1.1 | Active | Blocked |
| CO | [Internal Order and Project Control Assistant](agents/CO/internal-order-project-control/README.md) | 0.4.0 | Active | Blocked |
| CO | [Production Order Cost Variance Analysis Assistant](agents/CO/product-cost-variance/README.md) | 0.2.0 | Active | Passed |
| Common | [Role-to-Agent Matching Assistant](agents/Common/role-agent-matching/README.md) | 0.2.0 | Active | Platform gate |
| FI | [AP Payment Assistant](agents/FI/ap-payment/README.md) | 0.2.1 | Active | Passed |
| FI | [AR Cash Application Reconciliation Assistant](agents/FI/ar-cash-application/README.md) | 0.1.0 | Active | Passed |
| FI | [AR Collection Assistant](agents/FI/ar-collection/README.md) | 1.2.0 | Active | Passed |
| FI | [GR/IR Clearing Assistant](agents/FI/gr-ir-clearing/README.md) | 0.2.1 | Active | Passed |
| FI | [Month-end Closing Assistant](agents/FI/month-end-closing/README.md) | 0.2.1 | Active | Passed |
| MM | [Intelligent Sourcing and RFQ Evaluation Assistant](agents/MM/intelligent-sourcing-rfq/README.md) | 0.1.1 | Active | Passed |
| MM | [Inventory Health Check](agents/MM/inventory-health-balancing/README.md) | 0.4.0 | Active | Passed |
| MM | [Material Shortage Procurement Response Assistant](agents/MM/material-shortage-procurement-response/README.md) | 0.2.1 | Active | Passed |
| MM | [Purchase order receipt status check](agents/Common/mm-po-gr-status/README.md) | 0.1.1 | Active | Passed |
| MM | [Procure-to-Pay Status Assistant](agents/MM/procure-to-pay-status/README.md) | 0.3.2 | Active | Passed |
| MM | [Supplier Performance and Delivery Risk Assistant](agents/MM/supplier-performance-risk/README.md) | 0.2.3 | Active | Passed |
| PP | [Planned Order and PIR Coverage Assistant](agents/PP/demand-forecast-planning/README.md) | 0.3.1 | Active | Passed |
| PP | [MRP Exception Analysis Assistant](agents/PP/mrp-exception-analysis/README.md) | 0.2.1 | Active | Passed |
| PP | [Production Order Execution Monitoring Assistant](agents/PP/production-order-monitoring/README.md) | 0.2.1 | Active | Passed |
| PP | [Production Scheduling & Capacity Assistant](agents/PP/production-scheduling-capacity/README.md) | 0.1.0 | Active | Blocked |
| PP | [Production Quantity and Material Variance Analysis Assistant](agents/PP/production-variance-analysis/README.md) | 0.2.1 | Active | Passed |
| SD | [Billing Block Diagnosis](agents/SD/billing-block-diagnosis/README.md) | 0.2.1 | Active | Passed |
| SD | [Billing Completeness Check](agents/SD/billing-completeness-check/README.md) | 0.1.1 | Active | Passed |
| SD | [Billing Dispute Classification](agents/SD/billing-dispute-classification/README.md) | 0.1.0 | Inactive | Blocked |
| SD | [Billing Output Monitor](agents/SD/billing-output-monitor/README.md) | 0.1.0 | Inactive | Blocked |
| SD | [Delivered-not-Billed Monitor](agents/SD/delivered-not-billed/README.md) | 0.2.1 | Active | Passed |
| SD | [Delivery Delay Prediction](agents/SD/delivery-delay-prediction/README.md) | 0.1.1 | Active | Passed |
| SD | [Due Delivery Prioritization](agents/SD/due-delivery-prioritization/README.md) | 0.1.1 | Active | Passed |
| SD | [New Sales Demand Coverage Check Assistant](agents/SD/new-sales-demand-coverage/README.md) | 0.1.0 | Active | Passed |
| SD | [Order-to-Cash Status](agents/SD/order-to-cash-status/README.md) | 0.1.2 | Active | Passed |
| SD | [Returns and Credit Anomaly Monitor](agents/SD/returns-credit-anomaly/README.md) | 0.1.0 | Active | Blocked |
| SD | [Shortage Allocation Advisor](agents/SD/shortage-allocation-advisor/README.md) | 0.1.0 | Active | Blocked |
<!-- generated:agents-en:end -->

### Development, testing and deployment

The [Developer guide](docs/developer-guide.md) covers architecture, schemas, interfaces, offline regression, catalog checks, contribution and static deployment. Historical acceptance reports retain their original dates and applicable versions; they do not certify a new environment.

<!-- Compatibility anchors retained for existing README links. -->
<a id="项目功能"></a><a id="当前-agent"></a><a id="仓库结构"></a><a id="agent-清单契约"></a>
<a id="单机双模式原型"></a><a id="运行依赖版本策略"></a><a id="本地开发"></a><a id="添加新的-agent"></a><a id="自动部署"></a>
<a id="features"></a><a id="current-agents"></a><a id="repository-structure"></a><a id="agent-manifest-contract"></a>
<a id="local-dual-mode-prototype"></a><a id="agent-runtime-selection"></a><a id="runtime-dependency-version-policy"></a><a id="local-development"></a><a id="add-a-new-agent"></a><a id="automated-deployment"></a>

原章节已整理到[开发指南](docs/developer-guide.md)和[首次使用指南](docs/getting-started.md)。 Former sections are now in the [Developer guide](docs/developer-guide.md) and [Getting started](docs/getting-started.md).
