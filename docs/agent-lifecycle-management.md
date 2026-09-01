# 固定 Agent 全生命周期管理 / Fixed-Agent lifecycle management

“Agent 管理中心”只管理具有确定性 `execution` 定义的固定 Agent。`platform_assistant`（例如“岗位匹配助理”）继续由平台代码维护，不进入此管理链。

管理流程分为四步：

1. **定义与修改**：可从空白模板、现有 Agent、成功自由查询或工作流能力缺口创建草稿；结构化编辑与 Codex Runtime 多轮反馈都会生成不可变修订。
2. **检查 Diff**：比较 Agent 清单、输入输出、固定步骤、SAP 工具、完整性规则、双语说明及受控规则代码。撤销通过新修订实现，不删除历史。
3. **验证**：静态检查拒绝写操作、未注册工具和危险 Python；行为变化必须执行 GET-only 真机验证。纯文案变化只有在执行摘要完全相同时才能复用原 PASS 验收。
4. **发布与启用**：平台判断最低语义版本等级，创建 `codex/agent-...` 本地分支并提交；不会推送。未启用版本不会进入普通业务 Agent 目录。

## 运行一致性

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
