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

每个业务理解、Agent 匹配、工作流建议和能力缺口都必须带材料来源引用。文档引用保留页码、工作表、幻灯片或行号；岗位描述引用明确显示“用户提供的岗位描述”和轮次。Runtime 只能建议当前目录中的 Agent；平台会丢弃未知 Agent ID，并对组合建议运行 compiler v4 的版本固定、端口类型、`oneOf`、基数、`runIf/onSkip`、完整性传播和只读检查。只有 `PASS/executable=true` 的 Agent 能进入工作流建议。

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
