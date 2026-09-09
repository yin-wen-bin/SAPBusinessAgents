# 固定 Agent 全生命周期管理 / Fixed-Agent lifecycle management

“Agent 管理中心”只管理具有确定性 `execution` 定义的固定 Agent。`platform_assistant`（例如“岗位匹配助理”）继续由平台代码维护，不进入此管理链。

管理流程分为三步：

1. **定义与修改**：按业务详情页呈现基础信息、输入输出、处理步骤及安全边界。可直接修改双语名称、说明和负责人；复杂定义放在“高级技术编辑”。参数输入、自动检查和只读试运行位于同一页，结果原位展示。页面最下方“您的修改意见是？”支持澄清、解释和多轮修改；只有内容实际变化才保存新修订，不自动发布。页面“创建 Agent”仍进入自由查询，已有草稿和正式 Agent 新版本沿用现有入口。
2. **检查修改内容**：选择两个已保存修订，直接查看修改前、修改后内容，文档与规则提供逐行差异。不是只显示路径或摘要，也不把草稿比较冒充与当前活动版本比较。恢复通过创建一个新修订实现，不删除历史。
3. **发布与启用**：自动检查、试运行、正式验收分别展示。试运行成功不生成独立基线或自由查询 `MATCH`，不取得发布资格。行为变化仍须满足正式验收门禁；纯文案仅在执行摘要不变且原验收有效时复用。平台判断最低语义版本等级，发布创建本地分支和提交，不自动推送。

### 先找样本，再确认试运行

勾选“自动查找验证数据”后，先输入公司代码、工厂等必要范围，再点击查找。平台使用已通过兼容检查的 `gpt-5.6-sol` 和受控只读能力，在草稿声明的数据源内有界选样（最多300秒、10次数据读取、每次最多100行）。候选值必须来自本次已校验的公开证据；用户已填写的范围不会被覆盖。未能证明完整输入组合、分支不明确或需要敏感参考号时，提示手工补充，不猜测数据。

候选参数先回填并显示来源，确认后才能试运行。选样不代表数据源查询完整，也不是正式验收；试运行重新执行草稿快照。修改定义或参数后，旧选样确认失效。查找和对话可取消，刷新后恢复持久化状态；服务器重启中断的非可重放任务不会自动扩大查询或再次执行。

同一草稿同时只允许一个修改、选样或验证操作；修订冲突要求刷新核对。敏感参数只通过专用字段传递，不能放入修改对话。模型、SDK和默认Runtime设置不由编辑页面静默更改。

## 运行一致性

### 统一列表与加载

正式 Agent 与未发布草稿使用独立记录键，按模块、名称排序，同名正式版本优先，草稿按更新时间倒序。模块、状态、验收三个筛选默认全部，组合条件取交集。草稿“已验证”等编辑进度与实际验收 `validation.verdict` 分开显示；空验收归为“未记录”，不会由草稿状态推断通过。

目录与草稿分别加载、重试和保留上次成功快照。一侧失败不隐藏另一侧；缓存失败标记过期，首次失败不当作空列表，计数明确标记数据不完整。进入详情再返回保留筛选，整页刷新重置；不把筛选写入 URL。现有 `?agent=`、`?draft=`、`&step=` 深链接继续可用。

页面进入、返回列表、重新获得焦点和手动刷新都会读取最新数据。页面可见且有验证中草稿时每 5 秒刷新草稿；失败逐步退避至 30 秒，隐藏或卸载时停止。请求合并且只应用最新响应。服务端列表同步仅读取已有验证运行，通过草稿修订、运行 ID 和当前状态的原子条件更新保存终态；缺失运行显示同步错误，旧结果不能覆盖新修订或新验证。

### 自由查询草稿接入

`POST /api/authoring/drafts/{draft_id}/import-to-management` 只接受已登记源草稿 ID，不接受文件路径。服务端检查受控目录与文件链接，完整导入规则、双语说明和附件，保留源 ID、运行 ID、会话、已接受修订、结果摘要、证据引用及工作流缺口来源。导入不调用模型，不重新执行 SAP 查询。

单次运行和会话创建响应保留原字段并增加 `managed_draft_id`、`management_import_status`。新管理草稿总是 `needs_review` / `NOT_TESTED`；源检查结果只保存在来源元数据中。源运行缺失时保留源 ID、标记结果不可用，不推断验收。

生成按运行、修正、来源（会话包含已接受修订和结果摘要）保存持久化幂等键；导入按源草稿保存唯一映射。重复点击、并发请求和重试复用结果。生成成功、导入失败会明确提示并允许只重试导入，成功后提供“管理此草稿”链接。已接入源草稿仍可读取，其旧修改、检查和应用接口返回 HTTP 409 及管理草稿 ID；后续编辑与发布只在管理草稿进行。不批量迁移历史草稿。

原始源包、管理包、来源元数据和业务样本仍处于忽略目录。发布复用既有规则安全和验收门禁；导入包的原始附件、业务样本和额外来源文件不复制到发布目录，仅将生成的规则、双语说明及数据契约等受控文件交付。完整附件仍可在本地管理草稿包中追溯。

每个固定 Agent 运行会在创建时保存完整 Agent 清单、版本、Digest 和自有规则源码。已发布工作流按 `agentId + agentVersion + agentDigest`读取历史版本，因此 Agent 升级后，旧运行与旧工作流不会静默切换到新规则。

停用会阻止新的直接运行和工作流运行，但不会取消已开始的任务。回滚只能选择仍具有有效 PASS 验收的历史版本；SAP 元数据或工具契约发生漂移时必须重新验证。

## 受控规则

Agent 自有 `rules.py`必须只公开 `evaluate(inputs)`，只能处理平台传入的结构化证据。平台使用 AST 允许列表拒绝网络、文件写入、进程、环境变量、动态导入、反射及 `eval/exec`，并在精简环境的隔离 Python 进程中执行，设置固定超时和输出上限。规则源码与 SHA-256 一并进入运行快照。

## 删除门禁

永久删除必须同时满足：Agent 已停用；没有排队或运行中的任务；没有活动草稿或验证；没有正式工作流引用任何版本；页面版本和 Digest 未变化；用户输入完整 Agent ID；Git 工作区干净。删除不改写历史运行、审计记录或 Git 历史。

主要接口：

```text
GET    /api/agents/catalog?state=active|inactive|all
GET    /api/agents/{agent_id}/versions
GET    /api/authoring/agents?state=unpublished
POST   /api/authoring/drafts/{draft_id}/import-to-management
POST   /api/authoring/agents
PUT    /api/authoring/agents/{draft_id}
POST   /api/authoring/agents/{draft_id}/feedback
GET    /api/authoring/agents/{draft_id}/conversation
POST   /api/authoring/agents/{draft_id}/feedback/{turn_number}/cancel
GET    /api/authoring/agents/{draft_id}/diff?fromRevision=1&toRevision=2
POST   /api/authoring/agents/{draft_id}/validate
POST   /api/authoring/agents/{draft_id}/live-validate
GET    /api/authoring/agents/{draft_id}/validation-report
POST   /api/authoring/agents/{draft_id}/sample-discovery
GET    /api/authoring/agents/{draft_id}/sample-discovery/{run_id}
POST   /api/authoring/agents/{draft_id}/sample-discovery/{run_id}/cancel
POST   /api/authoring/agents/{draft_id}/publish
POST   /api/agents/{agent_id}/activate
POST   /api/agents/{agent_id}/deactivate
POST   /api/agents/{agent_id}/rollback
DELETE /api/agents/{agent_id}
```

## 回归验证

- `tests/test_agent_lifecycle.py`、`tests/test_platform_runtime.py`：真实平台服务配合隔离的假 SAP Provider，覆盖持久化幂等、并发导入、失败恢复、完整包、路径门禁、旧接口、来源链及三类验证终态。不会执行真实 SAP 查询。
- `site/tests/agent-management.test.mjs`：筛选交集、独立身份、验收语义、分源缓存、请求合并、乱序响应和退避。
- `site/tests/browser/agent-management.mjs`：本地服务实际读取目录和草稿，在中英文 1440×900、772×698、390×844 交互检查；失败、轮询与创建/导入链使用浏览器隔离响应，不修改现有业务草稿或执行 SAP。截图在忽略的 `.local-data/ui/agent-management/`。

浏览器回归使用已安装的 Playwright 和 Edge；可设置 `SAPBA_PLAYWRIGHT_MODULE` 为 Playwright 包路径，`SAPBA_SITE_URL` 为本地站点地址，然后在仓库根目录运行：

```text
node site/tests/browser/agent-management.mjs
```

2026-09-06 本轮回归：完整 Python 为 661 passed / 1 skipped；跳过项是本机无符号链接创建权限的外部附件测试，登记路径越界与拒绝路径参数测试已通过。站点 `validate`、`check`（0 errors / warnings）及 38 项测试通过；9 组浏览器检查覆盖两种语言、三种视口、加载/状态场景及会话/单次运行创建闭环。本轮接入与状态同步测试没有新增 SAP 业务查询。

## 文案版本与原验收 / Documentation versions and original acceptance

只修改README或共享翻译不递增Agent版本。修改正式清单摘要/步骤文案时，具有可复用PASS的固定Agent走patch发布；执行、Schema和规则摘要必须完全相同。保留原验收模式、日期、Provider、比较结果、范围、限制和报告路径，单独记录documentationReuse来源版本/摘要与文案复核时间。复核时间不是新的SAP验收日期。未验收版本保留草稿，停用版本不会因改文案而启用。

README-only or shared-translation changes do not bump Agent versions. Manifest summary/step copy uses a patch release only with reusable PASS acceptance and unchanged execution/schema/rules. Preserve original mode, date, Provider, comparisons, scope, limitations and report path; record documentationReuse provenance and review time separately. Review time is not a new SAP test date. Unaccepted changes remain drafts and inactive Agents are not activated by editing copy.

## English user guide

Use Agent management for deterministic fixed Agents, not platform assistants. The three stages are **Define and edit**, **Review changes**, and **Publish and activate**. The business-oriented editor includes typed inputs, static checks, read-only trials and inline results; technical JSON and rules are collapsed. The bottom feedback conversation can clarify, reply without editing, or create an immutable revision. Compare actual before/after values and text differences between saved revisions.

Optional sample discovery uses compatible `gpt-5.6-sol`, scoped to the draft's approved read-only sources and your supplied company/plant filters. It is bounded to 300 seconds, 10 data reads and 100 rows per read, and every proposed value needs verified public evidence. Confirm proposed inputs before trial execution. Ambiguous input branches, unproven relationships and sensitive references require manual entry. Discovery is not exhaustive evidence or formal acceptance. A successful trial cannot manufacture independent comparison results or unlock publication. Refresh restores saved task state; edits invalidate stale sample confirmations. No SDK upgrade or global model setting is changed.

Draft deletion requires exact Agent ID confirmation and revision checks; published versions use the separate stricter permanent-deletion gate. Validation runs and their evidence survive draft deletion.

An active Agent and an accepted Agent are distinct states. Local publication creates a branch and commit but never pushes. Running tasks retain snapshots, and published workflows retain pinned historical versions. Deactivation blocks new work; it does not cancel running tasks. See the Chinese technical sections above for interface IDs and regression entry points.
