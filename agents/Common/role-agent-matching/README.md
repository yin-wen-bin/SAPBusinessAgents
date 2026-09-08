# 岗位匹配助理 / Role-to-Agent Matching Assistant

## 用途与使用场景 / Purpose and scenario

用岗位描述、本地文档或两者组合，梳理职责、SAP操作、Agent匹配、工作流建议和能力缺口。示例：描述仓库实习生负责收货核对与库存盘点，再核对建议对应的原文。

Use a role description, local documents, or both to identify responsibilities, SAP operations, Agent matches, workflow suggestions and gaps. Example: describe an intern reconciling receipts and counting stock, then inspect the supporting passages.

## 如何开始 / Getting started

从本地网页进入，选择“岗位描述”“文档路径”或“两者同时使用”。确认发送材料正文后开始；先复核提取的职责，再检查每项Agent匹配的来源及覆盖范围。它是平台助理，不受固定Agent发布门禁管理，但仍需岗位匹配能力已通过验收的Runtime。

Open the local page and choose description, document paths, or both. Consent to sending source text, then review extracted responsibilities before source citations and coverage for each match. This platform assistant uses the accepted role-matching Runtime capability rather than the fixed-Agent publication gate.

## 结果解读与下一步 / Reading results and next steps

两类材料至少提供一项；描述最多12000字符，路径最多100项。确认发送正文给Runtime后才开始。用户描述有明确来源标记，不自动成为SAP事实；目录不完整时不作确定性缺口结论。多轮补充与排除来源保留旧修订。

Provide at least one source: up to 12,000 description characters and 100 paths. Consent to send source text to Runtime is required. User descriptions remain labeled user sources, not SAP facts; incomplete catalogs cannot establish definitive gaps. Added or excluded sources create new revisions.

完整检查目录后才判断能力缺口。full/partial显示为匹配，none保留为已排除候选；目录变化后可全量重新匹配。工作流建议须通过精确Agent ID、版本、端口、可执行性与只读检查，不自动创建或启用正式工作流。

Capability gaps require a fully evaluated catalog. Full/partial are matches; none remains rejected audit detail. Full rematching can use a changed catalog. Workflow suggestions must pass exact ID/version/port, executability and read-only checks; they do not create or activate a published workflow automatically.

## 当前范围与输入输出 / Current scope and I/O

<!-- generated:facts:start -->
版本 / Version: **0.2.0** · 使用中 / Active · 平台助理：由 Runtime 能力门禁控制 / Platform assistant: Runtime capability gate

网页 / Web: [zh](http://127.0.0.1:4321/zh/agents/Common/role-agent-matching/) · [en](http://127.0.0.1:4321/en/agents/Common/role-agent-matching/)


### 输入 / Inputs

| 字段 / Field | 名称 / Name | 要求 / Requirement | 类型 / Type | 约束 / Constraints |
|---|---|---|---|---|
| `roleDescription` | 岗位描述（与路径至少提供一项） / Role description (or paths required) | 可选 / Optional | `string` | {"maxLength": 12000} |
| `paths` | 文档路径（与描述至少提供一项） / Document paths (or description required) | 可选 / Optional | `array` | {"maxItems": 100} |
| `consentToRuntime` | 确认发送正文 / Consent to send source text | 必填 / Required | `boolean` | {"enum": [true]} |

约束中的 default 是默认值；示例不是 SAP 测试样本。条件必填规则以网页提示和完整 Schema 为准。 / `default` denotes a default, not live test data. Conditional requirements are defined by the form and full Schema.

### 结果字段 / Result fields

- 岗位、流程与SAP操作清单 / Roles, processes and SAP operations
- Agent匹配清单 / Agent matches
- 工作流建议 / Workflow suggestions
- Agent能力缺口 / Agent capability gaps
- 带来源引用的分析报告 / Source-cited analysis report

### 数据与开发资料 / Contracts and development

- [Agent 定义与完整输入输出契约 / Manifest and complete I/O contract](agent.json)
- [开发指南 / Developer guide](../../../docs/developer-guide.md)
<!-- generated:facts:end -->
