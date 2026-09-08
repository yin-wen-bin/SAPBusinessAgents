# 开发指南 / Developer guide

## 仓库与职责 / Repository and ownership

| 路径 / Path | 内容 / Content |
|---|---|
| agents/&lt;module&gt;/&lt;id&gt;/ | 当前Agent投影、规则、契约和使用说明 / Current projection, rules, contracts and guidance |
| versions/（各Agent或工作流下） | 不可变版本包 / Immutable version packages |
| src/sap_business_agents_platform/ | FastAPI、SQLite、SSE、执行与安全门禁 / API, storage, execution and safety gates |
| site/ | Astro目录与本地操作界面 / Astro catalog and local UI |
| config/ | 受控API、Skill、SDK和插件声明 / Reviewed API, Skill, SDK and plugin declarations |
| docs/ | 用户指南、架构及历史验收 / User guides, architecture and historical acceptance |

先阅读[首次使用](getting-started.md)。Python要求由[pyproject.toml](../pyproject.toml)定义，Node要求与命令见[site/package.json](../site/package.json)。全新环境必须安装前后端依赖。 / Begin with [Getting started](getting-started.md). Python requirements live in pyproject.toml; Node and frontend commands live in site/package.json. Install both dependency sets in a fresh environment.

## 契约、接口与架构 / Contracts, APIs and architecture

- [Agent生命周期与管理接口 / Agent lifecycle and management](agent-lifecycle-management.md)
- [OData目录、元数据与只读Provider / OData catalog and read-only Provider](odata-catalog-v2.md)
- [Codex Harness及证据工具 / Harness and evidence tools](codex-harness.md)
- [工作流使用与接口 / Workflows and interfaces](user-workflows.md)
- [插件内核 / Plugin platform](local-plugin-platform.md) · [连接与邮件能力 / Connections and mail](runtime-integrations.md)
- [岗位匹配 / Role matching](role-agent-matching.md) · [AR安全证据 / AR evidence](ar-collection-cash-application.md)

当前工作流编译器按v5实现维护。类型化端口、foreach、runIf、onSkip、运行快照和固定版本解析由平台确定性校验；自然语言建议不是可执行代码。用户指南不以编译器编号组织操作。 / The current workflow compiler is v5. Typed ports, foreach, runIf, onSkip, snapshots and pinned-version resolution are validated deterministically. Natural-language suggestions are not executable code; user instructions are organized by task rather than compiler number.

固定Agent修改走生命周期流程；纯文案复用完整原验收信息，并单独记录来源版本、摘要及复核时间。不改写原SAP验收日期，不凭文档测试补造MATCH。执行、Schema或规则变化必须重新验收。 / Use the lifecycle for fixed-Agent changes. Documentation-only releases preserve complete prior acceptance and separately record provenance and review time. Never replace the original SAP date or invent MATCH from documentation checks. Execution, schema or rule changes require acceptance.

## 开发与回归 / Development and regression

在仓库根目录 / From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\documentation.py --check
cd site
npm run validate
npm test
npm run check
npm run build
```

`documentation.py --write`只刷新当前README中的受控索引、状态及输入输出区块；人工叙述留在区块外。`--check`检查索引漂移、当前说明覆盖与本地文件链接，忽略versions及归档报告。历史README迁移到Agent的docs/offline-regression.md，不构成生产入口或新验收。 / `--write` refreshes bounded generated indexes/status/I/O only; keep human narrative outside markers. `--check` checks drift, current coverage and local file links, excluding versions and archived reports. Agent docs/offline-regression.md preserves historical CLI use without establishing production behavior or new acceptance.

新增Agent先建立规范目录、双语说明、输入输出与确定性执行定义，补齐fixture及只读验收。使用Agent管理发布；BLOCKED/NOT_TESTED不可绕过门禁。修改业务规则不能夹带在文案补丁中。 / New Agents require localized docs, I/O and deterministic execution definitions, fixtures and read-only acceptance. Publish through Agent management; never bypass BLOCKED/NOT_TESTED gates or hide behavior changes in a documentation patch.

## 部署与贡献 / Deployment and contribution

本地启动器提供真实操作界面；GitHub Pages仅发布静态目录，不部署API或凭据。开发站点使用根路径，生产静态站点使用仓库base路径。main推送触发的校验/部署以[CI配置](../.github/workflows)为准；PR仅做审核与检查。 / The local launcher provides the operational UI; GitHub Pages publishes only the static catalog, not APIs or credentials. Development uses the root path; production static builds use the repository base. See CI workflows for push/deploy rules; PRs serve review and checks.

提交前检查`.env`、本地数据库、制品和原始SAP数据未进入Git。历史运行和版本不可变，已发布工作流不会随Agent升级自动重新绑定。 / Check that credentials, local databases, artifacts and raw SAP data are not staged. Runs and versions are immutable; released workflows do not automatically rebind after Agent upgrades.
