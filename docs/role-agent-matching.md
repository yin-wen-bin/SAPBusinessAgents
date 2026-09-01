# 岗位匹配助理

`role-agent-matching` 是 Common 模块中的 Runtime 驱动平台助理。它只读取用户明确选择的本地文件或目录，识别材料中的 SAP 岗位、流程和日常操作，再对当前 Agent 目录进行确定性复核后的能力匹配。

## 安全边界

- 仅接受 Windows 绝对路径或 UNC 路径，不跟随符号链接或目录联接。
- 支持带文本层的 PDF、DOCX、XLSX、PPTX、TXT、MD、CSV、JSON 和 YAML；第一版不做 OCR，也不读取旧 Office 二进制格式。
- 不执行宏、公式、外部链接或嵌入代码。
- 用户确认前不读取正文、不调用 Runtime；Codex 只收到文档 ID、结构化位置和正文，不收到本地完整路径。
- 正文只保存在 `.local-data/role-matching/<session_id>/`，不写入 SQLite、SSE、应用日志或 Git。
- 助理不调用 SAP，也不修改原始材料、Agent 或工作流。

## 输出与复核

每个业务理解、Agent 匹配、工作流建议和能力缺口都必须带文档位置引用。Runtime 只能建议当前目录中的 Agent；平台会丢弃未知 Agent ID，并对组合建议运行 compiler v4 的版本固定、端口类型、`oneOf`、基数、`runIf/onSkip`、完整性传播和只读检查。只有 `PASS/executable=true` 的 Agent 能进入工作流建议。

## 多轮会话

用户可以增加路径、排除材料、修正业务理解，并选择增量或全量重新匹配。每一轮形成不可变修订：增量模式复用未变化文件的解析缓存，目录内容或 Agent 目录摘要变化时重新计算受影响的匹配；全量模式基于当前有效材料重新生成全部结果。

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
