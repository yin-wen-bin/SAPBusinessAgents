# 固定 Agent 全生命周期管理 / Fixed-Agent lifecycle management

“Agent 管理中心”只管理具有确定性 `execution` 定义的固定 Agent。`platform_assistant`（例如“岗位匹配助理”）继续由平台代码维护，不进入此管理链。

管理流程分为四步：

1. **定义与修改**：页面“创建 Agent”进入当前语言的自由查询，已生成草稿接入管理中心后再复核；正式 Agent 仍可“创建新版本”。结构化编辑与 Codex Runtime 多轮反馈都会生成不可变修订。后端共享的空白、复制和工作流缺口能力保留，管理页不再提供旧创建表单。
2. **检查 Diff**：比较 Agent 清单、输入输出、固定步骤、SAP 工具、完整性规则、双语说明及受控规则代码。撤销通过新修订实现，不删除历史。
3. **验证**：静态检查拒绝写操作、未注册工具和危险 Python；行为变化必须执行 GET-only 真机验证。纯文案变化只有在执行摘要完全相同时才能复用原 PASS 验收。
4. **发布与启用**：平台判断最低语义版本等级，创建 `codex/agent-...` 本地分支并提交；不会推送。未启用版本不会进入普通业务 Agent 目录。

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
POST   /api/authoring/agents/{draft_id}/validate
POST   /api/authoring/agents/{draft_id}/live-validate
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
