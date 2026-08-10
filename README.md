# SAP Business Agents

[中文](#中文) | [English](#english) | [在线目录 / Live catalog](https://yin-wen-bin.github.io/SAPBusinessAgents/)

SAP Business Agents 是一个按 SAP 业务模块组织的可运行 Agent 目录。它参照 [SAPSkillhub](https://github.com/yin-wen-bin/SAPSkillhub) 的内容组织方式：站点自动扫描 `agents/` 下的清单，生成模块导航、搜索、双语详情页，以及工作流步骤与 Tool 的映射，无需维护手写中央索引。

## 中文

### 项目功能

- 按 Common、FI、CO、SD、MM、PP 组织 SAP 业务 Agent。
- 每个 Agent 是独立、可运行、可测试的业务纵向切片。
- 中英文目录支持按模块、事务码、表、Tool 和业务关键词搜索。
- Agent 详情页展示输入、输出、SAP 范围、安全边界和完整工作流。
- 工作流的每一步明确列出实际使用的 Tool、数据入口或控制组件。
- 高风险 SAP 动作保持只读、人工确认和可审计边界。

在线目录：[https://yin-wen-bin.github.io/SAPBusinessAgents/](https://yin-wen-bin.github.io/SAPBusinessAgents/)

### 当前 Agent

| 模块 | Agent | 业务场景 |
|---|---|---|
| FI | [AP Payment Assistant](agents/FI/ap-payment/) | 供应商付款状态、未清项目与付款风险 |
| FI | [AR Collection Assistant](agents/FI/ar-collection/) | 应收账龄、收款匹配与催收建议 |
| FI | [GR/IR Clearing Assistant](agents/FI/gr-ir-clearing/) | GR/IR 未清原因、证据与清理建议 |
| FI | [Month-end Closing Assistant](agents/FI/month-end-closing/) | FI/CO/MM/SD 月结异常检查与关账待办 |
| MM | [Procure-to-Pay Status Assistant](agents/MM/procure-to-pay-status/) | PO → GR → IV → FI → Payment 全链路状态 |
| SD | [Delivered-not-Billed Monitor](agents/SD/delivered-not-billed/) | 已发货未开票识别与滞留分级 |
| SD | [Billing Block Diagnosis](agents/SD/billing-block-diagnosis/) | 订单、项目与交货开票冻结诊断 |
| SD | [Billing Completeness Check](agents/SD/billing-completeness-check/) | 数量、价格、币种与税务完整性检查 |
| SD | [Billing Output Monitor](agents/SD/billing-output-monitor/) | 发票输出失败与客户送达监控 |
| SD | [Delivery Delay Prediction](agents/SD/delivery-delay-prediction/) | 可解释交货延期风险评分 |
| SD | [Due Delivery Prioritization](agents/SD/due-delivery-prioritization/) | 到期交货清单智能排序 |
| SD | [Shortage Allocation Advisor](agents/SD/shortage-allocation-advisor/) | 缺货场景的只读库存分配建议 |
| SD | [Billing Dispute Classification](agents/SD/billing-dispute-classification/) | 客户拒票与发票争议分类 |
| SD | [Returns and Credit Anomaly Monitor](agents/SD/returns-credit-anomaly/) | 退货及贷项异常检测 |
| SD | [Order-to-Cash Anomaly Monitor](agents/SD/order-to-cash-anomaly-monitor/) | O2C端到端异常聚合与待办 |
| SD | [Order-to-Cash Status](agents/SD/order-to-cash-status/) | 订单、交货、开票、FI与回款状态 |

### 仓库结构

```text
agents/
  Common/
  FI/
  CO/
  SD/
  MM/
  PP/
site/                 # Astro 静态目录站点
.codex/agents/        # 项目级 Custom Agent 配置
.github/workflows/    # 校验与 GitHub Pages 部署
```

每个 Agent 遵守以下目录契约：

```text
agents/<模块>/<agent-slug>/
  agent.json   # 目录、详情、工作流和 Tool 映射的数据源
  README.md    # 实现、运行和 SAP 接入说明
  src/         # Agent 实现
  tests/       # 自动化测试
  docs/        # 可选：数据契约或运行手册
  tools/       # 可选：受控辅助工具
```

### Agent 清单契约

`agent.json` 必须使用 `schemaVersion: 1`，其 `slug` 和 `module` 必须与目录一致，并提供：

- 中英文标题与摘要；
- 负责人、版本、状态、标签和适用系统；
- SAP 模块、事务码和核心对象或表；
- 中英文输入、输出和安全边界；
- 至少一个工作流步骤；
- 每个步骤至少一个 Tool，并描述 Tool 类型和中英文用途；
- 可选的步骤级 `sapScope` 将业务模块、事务码和核心对象/表映射到实际工作流步骤；启用后必须覆盖 Agent 的完整 SAP 范围。

站点在构建前校验全部清单。新增或修改 `agent.json` 后，目录页和详情页会自动更新。

### 运行依赖版本策略

- GitHub Actions 的测试基线为 Python 3.13、Node.js 22 和锁文件中的 npm 依赖版本。
- `site/package-lock.json` 是站点的可复现依赖基线，CI 使用 `npm ci`。
- Agent 如有额外 Python 依赖，应在自己的目录中声明并固定测试版本。

### 本地开发

运行全部 Agent 测试：

```powershell
python -m pytest -q
```

运行站点校验、类型检查和静态构建测试：

```powershell
cd site
npm ci
npm run validate
npm run check
npm test
```

本地预览：

```powershell
npm run dev
```

开发服务器使用根路径 `/`；生产构建自动使用 GitHub Pages 基础路径 `/SAPBusinessAgents/`。

### 添加新的 Agent

1. 在正确模块下创建 `agents/<module>/<slug>/`。
2. 添加符合契约的 `agent.json`、实现、测试和 README。
3. 确保每个工作流步骤都列出实际使用的 Tool。
4. 从仓库根目录运行 `python -m pytest -q`。
5. 在 `site/` 下运行 `npm run validate`、`npm run check` 和 `npm test`。
6. 提交变更；站点会自动发现新 Agent，无需修改主页索引。

### 自动部署

推送到 `main` 后，GitHub Actions 会测试所有 Agent、校验清单、检查 Astro 项目、构建静态站点并部署到 GitHub Pages。Pull Request 只执行校验和构建，不发布站点；工作流也支持手动触发。

## English

SAP Business Agents is a runnable catalog organized by SAP business module. Following the repository conventions of [SAPSkillhub](https://github.com/yin-wen-bin/SAPSkillhub), the site discovers manifests under `agents/` and generates module navigation, search, localized detail pages, complete workflows, and step-level Tool mappings without a handwritten central index.

### Features

- Organizes SAP business agents under Common, FI, CO, SD, MM, and PP.
- Keeps every Agent as an isolated, runnable, and testable vertical slice.
- Searches by module, transaction, table, Tool, or business keyword in Chinese and English.
- Shows inputs, outputs, SAP scope, guardrails, and the complete workflow on every detail page; curated step-level SAP scope is rendered inside the corresponding workflow step.
- Names the actual Tool, data surface, or control component used at every workflow step.
- Preserves read-only, human-confirmation, and audit boundaries for high-risk SAP actions.

Live catalog: [https://yin-wen-bin.github.io/SAPBusinessAgents/](https://yin-wen-bin.github.io/SAPBusinessAgents/)

### Current Agents

| Module | Agent | Business scenario |
|---|---|---|
| FI | [AP Payment Assistant](agents/FI/ap-payment/) | Vendor payment status, open items, and payment risk |
| FI | [AR Collection Assistant](agents/FI/ar-collection/) | AR aging, receipt matching, and collection advice |
| FI | [GR/IR Clearing Assistant](agents/FI/gr-ir-clearing/) | GR/IR open-item causes, evidence, and clearing advice |
| FI | [Month-end Closing Assistant](agents/FI/month-end-closing/) | FI/CO/MM/SD close checks and traceable follow-up work |
| MM | [Procure-to-Pay Status Assistant](agents/MM/procure-to-pay-status/) | End-to-end PO → GR → IV → FI → Payment status |
| SD | [Delivered-not-Billed Monitor](agents/SD/delivered-not-billed/) | Delivered-but-unbilled detection and ageing |
| SD | [Billing Block Diagnosis](agents/SD/billing-block-diagnosis/) | Billing-block and incompletion diagnosis |
| SD | [Billing Completeness Check](agents/SD/billing-completeness-check/) | Quantity, price, currency and tax validation |
| SD | [Billing Output Monitor](agents/SD/billing-output-monitor/) | Invoice-output delivery monitoring |
| SD | [Delivery Delay Prediction](agents/SD/delivery-delay-prediction/) | Explainable delivery-delay risk scoring |
| SD | [Due Delivery Prioritization](agents/SD/due-delivery-prioritization/) | Due-delivery worklist prioritization |
| SD | [Shortage Allocation Advisor](agents/SD/shortage-allocation-advisor/) | Read-only shortage allocation advice |
| SD | [Billing Dispute Classification](agents/SD/billing-dispute-classification/) | Billing rejection and dispute classification |
| SD | [Returns and Credit Anomaly Monitor](agents/SD/returns-credit-anomaly/) | Returns and credit anomaly detection |
| SD | [Order-to-Cash Anomaly Monitor](agents/SD/order-to-cash-anomaly-monitor/) | End-to-end O2C anomaly worklist |
| SD | [Order-to-Cash Status](agents/SD/order-to-cash-status/) | Sales order through FI clearing status |

### Repository structure

```text
agents/<module>/<agent-slug>/  # Manifest, implementation, tests, and docs
site/                          # Astro static catalog
.codex/agents/                 # Project-scoped Custom Agent definitions
.github/workflows/             # Validation and GitHub Pages deployment
```

### Agent manifest contract

Every `agent.json` uses schema version 1 and provides localized metadata, SAP scope, inputs, outputs, guardrails, and at least one workflow step. Every step must declare at least one Tool with its kind and localized purpose. A workflow may additionally declare step-level `sapScope` mappings; when enabled, those mappings must cover every module, transaction, and table in the Agent scope. The build validates all manifests before generating the catalog.

### Runtime dependency version policy

- CI uses Python 3.13, Node.js 22, and the npm versions pinned by `site/package-lock.json`.
- CI installs the site with `npm ci` for reproducible builds.
- Agent-specific Python dependencies belong in the Agent directory with a pinned tested baseline.

### Local development

```powershell
python -m pytest -q
cd site
npm ci
npm run validate
npm run check
npm test
```

Use `npm run dev` for the local site. Development uses `/`; production builds use `/SAPBusinessAgents/`.

### Add a new Agent

1. Create `agents/<module>/<slug>/` under the correct SAP module.
2. Add a valid `agent.json`, implementation, tests, and README.
3. Declare the actual Tools used by every workflow step.
4. Run the Agent and site validation commands above.
5. Commit the directory. The catalog discovers it automatically.

### Automated deployment

Pushes to `main` test all Agents, validate the manifests, check and build the Astro site, and deploy the static artifact to GitHub Pages. Pull requests validate and build without deployment, and the workflow can also be started manually.
