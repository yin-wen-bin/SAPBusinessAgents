# SAP Business Agents

[中文](#中文) · [English](#english)

## 中文

### 产品用途与适用用户

SAP Business Agents 帮助财务、采购、销售、生产及业务支持人员，查询 SAP 业务状态、核对证据并形成可追踪的处理建议。固定 Agent 按已审核规则执行；自然语言功能负责理解和编排，不绕过平台的数据与安全检查。

SAP 访问限于 OData GET 或已批准的语义只读 ADT 查询。查询、催收建议和清账核对都不代表已经完成付款、催收或 SAP 过账。邮件发送是独立外部动作，必须逐次确认。

> 在线静态目录只能浏览能力说明，不能代表已连接你的 SAP。执行查询、管理 Agent 和保存运行记录需要启动下方的本地环境。

### 按任务选择入口

| 你想做什么 | 选择哪个入口 | 是否需要 Agent Runtime |
|---|---|---|
| 重复检查应收、采购、库存或生产状态 | 按模块选择固定 Agent | 不需要；仍须验收与连接可用 |
| 临时提出没有固定入口的问题 | 自由查询 | 需要 |
| 把多个固定 Agent 串成流程 | 我的工作流 | 自然语言编写需要；确定性执行不需要 |
| 根据岗位职责寻找现有能力 | 岗位匹配助理 | 需要；不访问 SAP |
| 创建、修改、验证或停用固定 Agent | Agent 管理 | 对话编写需要；结构化管理不需要 |
| 配置邮件、插件或外部连接 | 插件与连接 | 取决于所选能力 |

精选入口：[应收催收](agents/FI/ar-collection/README.md) · [银行来款核对](agents/FI/ar-cash-application/README.md) · [批量采购到付款](agents/MM/procure-to-pay-status/README.md) · [生产订单成本差异](agents/CO/product-cost-variance/README.md) · [MRP需求覆盖](agents/PP/demand-forecast-planning/README.md) · [新增销售需求模拟](agents/SD/new-sales-demand-coverage/README.md)。

### 首次安装与启动

Windows 本地运行；Python 最低 **3.11**，推荐与 CI 一致的 **3.13**；Node.js **22.13.0 或以上**（CI 使用 Node 22）。先安装 Git、Python、Node.js，并准备有只读权限的 SAP 连接。

在 PowerShell 中：

```powershell
git clone https://github.com/yin-wen-bin/SAPBusinessAgents.git
cd SAPBusinessAgents
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd site
npm ci
cd ..
Copy-Item .env.example .env
```

只在首次安装且不存在 `.env` 时执行最后一步。编辑本地 `.env`：填写 SAP 连接；需要已批准 Skill 时配置自己的 `SAPSKILLHUB_ROOT`。不要提交凭据，也不要把密码写入问题文本。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-SAPBusinessAgents.ps1
```

打开 [本地中文主页](http://127.0.0.1:4321/zh/)；本地 API 默认监听 `127.0.0.1:8765`。自然语言功能还需在“系统配置 → Agent Runtime与SDK”确认登录、刷新模型目录、检查兼容性、选择默认模型、启用并选择默认 Runtime。可选模型以当前 SDK 和账号返回的完整目录为准，不使用固定型号清单。

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
| 模块 / Module | Agent | 版本 / Version | 生命周期 / Lifecycle | 验收 / Acceptance |
|---|---|---|---|---|
| CO | [预算滚动预测助手](agents/CO/budget-rolling-forecast/README.md) | 0.1.1 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [CO 月结分配与结算助手](agents/CO/co-month-end-allocation-settlement/README.md) | 0.1.1 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [成本中心费用异常助手](agents/CO/cost-center-expense-anomaly/README.md) | 0.1.1 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [内部订单与项目控制助手](agents/CO/internal-order-project-control/README.md) | 0.4.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [生产订单成本差异分析助手](agents/CO/product-cost-variance/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| Common | [岗位匹配助理](agents/Common/role-agent-matching/README.md) | 0.2.0 | 使用中 / Active | 平台能力门禁 / Platform gate |
| FI | [应付账款付款助手](agents/FI/ap-payment/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [银行来款与应收核销核对助手](agents/FI/ar-cash-application/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [应收账款催收助手](agents/FI/ar-collection/README.md) | 1.2.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [GR/IR 清账助手](agents/FI/gr-ir-clearing/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [月结助手](agents/FI/month-end-closing/README.md) | 0.2.0 | 使用中 / Active | 尚未验收 / Not tested |
| MM | [智能寻源与 RFQ 评估助手](agents/MM/intelligent-sourcing-rfq/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| MM | [库存健康检查](agents/MM/inventory-health-balancing/README.md) | 0.4.0 | 使用中 / Active | 验收通过 / Passed |
| MM | [外购件短缺采购响应助手](agents/MM/material-shortage-procurement-response/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| MM | [采购到付款状态助手](agents/MM/procure-to-pay-status/README.md) | 0.3.1 | 使用中 / Active | 验收通过 / Passed |
| MM | [供应商绩效与交付风险助手](agents/MM/supplier-performance-risk/README.md) | 0.2.2 | 使用中 / Active | 验收通过 / Passed |
| PP | [计划订单与计划独立需求覆盖度助手](agents/PP/demand-forecast-planning/README.md) | 0.3.0 | 使用中 / Active | 验收通过 / Passed |
| PP | [MRP 异常分析助手](agents/PP/mrp-exception-analysis/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| PP | [生产订单执行监控助手](agents/PP/production-order-monitoring/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| PP | [生产排程与产能助手](agents/PP/production-scheduling-capacity/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| PP | [生产数量与物料差异根因分析助手](agents/PP/production-variance-analysis/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [开票冻结诊断](agents/SD/billing-block-diagnosis/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [发票完整性检查](agents/SD/billing-completeness-check/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [发票争议分类](agents/SD/billing-dispute-classification/README.md) | 0.1.0 | 已停用 / Inactive | 因证据或验收缺口受阻 / Blocked |
| SD | [发票输出监控](agents/SD/billing-output-monitor/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| SD | [已发货未开票监控](agents/SD/delivered-not-billed/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [交货延期预测](agents/SD/delivery-delay-prediction/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [到期交货优先级排序](agents/SD/due-delivery-prioritization/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [新增销售需求覆盖度检查助手](agents/SD/new-sales-demand-coverage/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [订单到收款状态助手](agents/SD/order-to-cash-status/README.md) | 0.1.1 | 使用中 / Active | 验收通过 / Passed |
| SD | [退货及贷项异常监控](agents/SD/returns-credit-anomaly/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| SD | [缺货分配建议](agents/SD/shortage-allocation-advisor/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
<!-- generated:agents-zh:end -->

### 开发、测试与部署

[开发指南](docs/developer-guide.md) 收录架构、Schema、接口、离线回归、目录校验、贡献及静态部署说明。历史验收报告按原日期和适用版本保留，不代表重新验证了当前环境。

## English

### Purpose and audience

SAP Business Agents helps finance, procurement, sales, production and support teams inspect SAP status, reconcile evidence and produce traceable follow-up advice. Fixed Agents run reviewed deterministic rules; natural-language features interpret and compose requests without bypassing platform checks.

SAP access is limited to OData GET or approved semantically read-only ADT queries. Query results, collection advice and reconciliation do not execute payments, dunning or SAP postings. Email sending is a separate external action requiring confirmation each time.

> The hosted static catalog describes capabilities; it is not a connection to your SAP system. Query execution, Agent management and persisted runs require the local environment below.

### Choose by task

| Your task | Entry point | Agent Runtime required? |
|---|---|---|
| Repeat an AR, purchasing, inventory or production check | A fixed Agent in its module | No; acceptance and connections still apply |
| Ask an ad-hoc question without a fixed entry | Free query | Yes |
| Combine fixed Agents into a process | My workflows | For natural-language authoring; not deterministic execution |
| Match job responsibilities to capabilities | Role matching assistant | Yes; it does not access SAP |
| Create, edit, validate or deactivate fixed Agents | Agent management | For conversational authoring; not structured management |
| Configure mail, plugins or external connections | Plugins and connections | Depends on the capability |

Selected tasks: [AR collection](agents/FI/ar-collection/README.md), [bank reconciliation](agents/FI/ar-cash-application/README.md), [batch P2P](agents/MM/procure-to-pay-status/README.md), [production-order costs](agents/CO/product-cost-variance/README.md), [MRP demand coverage](agents/PP/demand-forecast-planning/README.md), [new sales-demand simulation](agents/SD/new-sales-demand-coverage/README.md).

### First installation and startup

Use Windows locally. Python **3.11** is the minimum; **3.13** is recommended to match CI. Node.js must be **22.13.0 or newer** (CI uses Node 22). Install Git, Python and Node.js, and obtain a read-only SAP connection.

In PowerShell:

```powershell
git clone https://github.com/yin-wen-bin/SAPBusinessAgents.git
cd SAPBusinessAgents
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd site
npm ci
cd ..
Copy-Item .env.example .env
```

Run the final command only on first installation when `.env` does not exist. Edit the local `.env` with your SAP connection and, where approved Skills are needed, your own `SAPSKILLHUB_ROOT`. Never commit credentials or put passwords in questions.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-SAPBusinessAgents.ps1
```

Open the [local English home](http://127.0.0.1:4321/en/); the API listens on `127.0.0.1:8765` by default. For natural-language features, use System settings → Agent Runtime and SDK to confirm login, refresh the model catalog, check compatibility, choose a model, enable the Runtime and select the default Runtime. Model choices come from the installed SDK and account, not a fixed list.

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
| 模块 / Module | Agent | 版本 / Version | 生命周期 / Lifecycle | 验收 / Acceptance |
|---|---|---|---|---|
| CO | [Budget Rolling Forecast Assistant](agents/CO/budget-rolling-forecast/README.md) | 0.1.1 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [CO Month-End Allocation and Settlement Assistant](agents/CO/co-month-end-allocation-settlement/README.md) | 0.1.1 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [Cost Center Expense Anomaly Assistant](agents/CO/cost-center-expense-anomaly/README.md) | 0.1.1 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [Internal Order and Project Control Assistant](agents/CO/internal-order-project-control/README.md) | 0.4.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| CO | [Production Order Cost Variance Analysis Assistant](agents/CO/product-cost-variance/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| Common | [Role-to-Agent Matching Assistant](agents/Common/role-agent-matching/README.md) | 0.2.0 | 使用中 / Active | 平台能力门禁 / Platform gate |
| FI | [AP Payment Assistant](agents/FI/ap-payment/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [AR Cash Application Reconciliation Assistant](agents/FI/ar-cash-application/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [AR Collection Assistant](agents/FI/ar-collection/README.md) | 1.2.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [GR/IR Clearing Assistant](agents/FI/gr-ir-clearing/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| FI | [Month-end Closing Assistant](agents/FI/month-end-closing/README.md) | 0.2.0 | 使用中 / Active | 尚未验收 / Not tested |
| MM | [Intelligent Sourcing and RFQ Evaluation Assistant](agents/MM/intelligent-sourcing-rfq/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| MM | [Inventory Health Check](agents/MM/inventory-health-balancing/README.md) | 0.4.0 | 使用中 / Active | 验收通过 / Passed |
| MM | [Material Shortage Procurement Response Assistant](agents/MM/material-shortage-procurement-response/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| MM | [Procure-to-Pay Status Assistant](agents/MM/procure-to-pay-status/README.md) | 0.3.1 | 使用中 / Active | 验收通过 / Passed |
| MM | [Supplier Performance and Delivery Risk Assistant](agents/MM/supplier-performance-risk/README.md) | 0.2.2 | 使用中 / Active | 验收通过 / Passed |
| PP | [Planned Order and PIR Coverage Assistant](agents/PP/demand-forecast-planning/README.md) | 0.3.0 | 使用中 / Active | 验收通过 / Passed |
| PP | [MRP Exception Analysis Assistant](agents/PP/mrp-exception-analysis/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| PP | [Production Order Execution Monitoring Assistant](agents/PP/production-order-monitoring/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| PP | [Production Scheduling & Capacity Assistant](agents/PP/production-scheduling-capacity/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| PP | [Production Quantity and Material Variance Analysis Assistant](agents/PP/production-variance-analysis/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [Billing Block Diagnosis](agents/SD/billing-block-diagnosis/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [Billing Completeness Check](agents/SD/billing-completeness-check/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [Billing Dispute Classification](agents/SD/billing-dispute-classification/README.md) | 0.1.0 | 已停用 / Inactive | 因证据或验收缺口受阻 / Blocked |
| SD | [Billing Output Monitor](agents/SD/billing-output-monitor/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| SD | [Delivered-not-Billed Monitor](agents/SD/delivered-not-billed/README.md) | 0.2.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [Delivery Delay Prediction](agents/SD/delivery-delay-prediction/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [Due Delivery Prioritization](agents/SD/due-delivery-prioritization/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [New Sales Demand Coverage Check Assistant](agents/SD/new-sales-demand-coverage/README.md) | 0.1.0 | 使用中 / Active | 验收通过 / Passed |
| SD | [Order-to-Cash Status](agents/SD/order-to-cash-status/README.md) | 0.1.1 | 使用中 / Active | 验收通过 / Passed |
| SD | [Returns and Credit Anomaly Monitor](agents/SD/returns-credit-anomaly/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
| SD | [Shortage Allocation Advisor](agents/SD/shortage-allocation-advisor/README.md) | 0.1.0 | 使用中 / Active | 因证据或验收缺口受阻 / Blocked |
<!-- generated:agents-en:end -->

### Development, testing and deployment

The [Developer guide](docs/developer-guide.md) covers architecture, schemas, interfaces, offline regression, catalog checks, contribution and static deployment. Historical acceptance reports retain their original dates and applicable versions; they do not certify a new environment.

<!-- Compatibility anchors retained for existing README links. -->
<a id="项目功能"></a><a id="当前-agent"></a><a id="仓库结构"></a><a id="agent-清单契约"></a>
<a id="单机双模式原型"></a><a id="运行依赖版本策略"></a><a id="本地开发"></a><a id="添加新的-agent"></a><a id="自动部署"></a>
<a id="features"></a><a id="current-agents"></a><a id="repository-structure"></a><a id="agent-manifest-contract"></a>
<a id="local-dual-mode-prototype"></a><a id="agent-runtime-selection"></a><a id="runtime-dependency-version-policy"></a><a id="local-development"></a><a id="add-a-new-agent"></a><a id="automated-deployment"></a>

原章节已整理到[开发指南](docs/developer-guide.md)和[首次使用指南](docs/getting-started.md)。 Former sections are now in the [Developer guide](docs/developer-guide.md) and [Getting started](docs/getting-started.md).
