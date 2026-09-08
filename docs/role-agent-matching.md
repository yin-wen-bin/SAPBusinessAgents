# 岗位匹配助理

`role-agent-matching` 是 Common 模块中的 Runtime 驱动平台助理。用户可以只输入岗位描述、只选择本地文件或目录，或同时使用两类材料。系统识别其中的 SAP 岗位、流程和日常操作，再对当前 Agent 目录进行确定性复核后的能力匹配。

## 安全边界

- 文档来源仅接受 Windows 绝对路径或 UNC 路径，不跟随符号链接或目录联接。
- 岗位描述作为独立的“用户提供来源”，不得表述为已验证SAP事实或正式制度文档。
- 支持带文本层的 PDF、DOCX、XLSX、PPTX、TXT、MD、CSV、JSON 和 YAML；第一版不做 OCR，也不读取旧 Office 二进制格式。
- 不执行宏、公式、外部链接或嵌入代码。
- 用户确认前不读取或保存岗位描述正文、不读取文档正文、不调用 Runtime；Codex 只收到材料 ID、来源类型、结构化位置和正文，不收到本地完整路径。
- 岗位描述和文档正文只保存在 `.local-data/role-matching/<session_id>/`，不写入 SQLite、SSE、应用日志或 Git。
- 助理不调用 SAP，也不修改原始材料、Agent 或工作流。

## 输出与复核

每个业务理解、Agent匹配、工作流建议和能力缺口都须带来源引用。文档保留页码、工作表、幻灯片或行号，描述显示“用户提供的岗位描述”和轮次。主匹配表只展示full/partial；none进入折叠的已排除候选，不计入匹配数量。平台复核全部目录及其摘要；目录不完整时结果为临时结果，不生成确定性缺口，也不允许创建工作流草稿。只有PASS/executable=true、端口与版本契约有效的Agent可以进入工作流建议。

目录变化后，使用“使用当前完整目录重新匹配”生成新的全量修订；旧会话和来源引用保持不变。无需为了获得新目录而重写历史结果。

## 多轮会话

用户可以增加路径、补充新的岗位描述、排除任一材料来源、修正业务理解，并选择增量或全量重新匹配。每一轮形成不可变修订：增量模式复用未变化文件和既有描述来源，目录内容或 Agent 目录摘要变化时重新计算受影响的匹配；全量模式基于当前有效材料重新生成全部结果。

创建会话时，`paths`和`roleDescription`至少提供一项：

```json
{
  "paths": ["D:\\BusinessDocs\\Warehouse"],
  "roleDescription": "负责SAP收货、库存盘点和月末库存差异核对。",
  "locale": "zh",
  "consentToRuntime": true
}
```

后续补充的`message`是修正上下文；需要成为可引用来源的文字使用`addedRoleDescription`。

主要接口：

```text
POST /api/role-matching/preflight
POST /api/role-matching/sessions
GET  /api/role-matching/sessions/{session_id}
GET  /api/role-matching/sessions/{session_id}/events
GET  /api/role-matching/sessions/{session_id}/documents
GET  /api/role-matching/sessions/{session_id}/revisions
GET  /api/role-matching/sessions/{session_id}/revisions/{revision}/report.md
GET  /api/role-matching/sessions/{session_id}/revisions/{revision}/report.json
GET  /api/role-matching/sessions/{session_id}/revisions/{revision}/{kind}.csv
POST /api/role-matching/sessions/{session_id}/feedback
POST /api/role-matching/sessions/{session_id}/cancel
POST /api/role-matching/sessions/{session_id}/workflow-suggestions/{suggestion_id}/draft
DELETE /api/role-matching/sessions/{session_id}
```

默认限制为 500 个文件、单文件 50 MB、材料总计 1 GB、12 个 Runtime 轮次。当前只有 Codex Runtime 被允许执行岗位匹配。

## English quick guide

Provide role-description text, local documents, or both; at least one source is required. Descriptions allow up to 12,000 characters and paths up to 100 entries. Consent is required before source text is stored/read or sent to Runtime. Paths are not sent to Runtime. User descriptions remain explicitly labeled user sources, not SAP facts or formal policies. The assistant does not call SAP or modify documents, Agents or workflows.

Inspect source citations for each conclusion. Main matches contain full/partial only; none candidates are kept in collapsed audit detail and excluded from counts. An incomplete catalog produces provisional matching, no definitive capability gaps and no workflow-draft creation. Only executable accepted Agents with valid version/port contracts can support workflows. Choose full rematching with the current catalog after a catalog change; old revisions remain immutable.

Add descriptions or documents, exclude sources, or correct interpretations in subsequent rounds. Incremental mode reuses unchanged sources; full mode analyzes all active sources. Excluding every source is rejected before Runtime. Existing limits include 500 files, 50 MB/file, 1 GB total and 12 Runtime turns. Supported text-bearing formats are listed above; OCR, macros and executable embedded content are not processed. Current role matching requires the accepted Codex Runtime capability.
