# 月结助手只读证据运行手册

## 证据顺序

1. Embedded SAP OData Provider：读取 Released API 和实时 metadata，所有业务请求只允许 GET。
2. SAPSkillhub：API 缺少必要实体、字段或关联时，条件调用受信任的只读 Skill。
3. 人工 SE16N：只有自动证据仍不足且需要诊断时，使用 scope-bound、经复核并带 SHA-256 的 manifest。
4. 证据仍不完整：输出 `DATA_GAP` / `INCONCLUSIVE`，不得把缺失、超时、截断或空分支解释为零异常。

## Embedded Provider 验证

- `/api/health` 必须显示 `selected_provider=embedded` 和 `read_only=true`。
- `/api/providers/sap-read` 必须显示 `selected_plugin_id=embedded-sap-odata` 与 `automatic_fallback=false`。
- Agent manifest 的每个 `sap_read` 步骤必须声明 `http_method=GET`。
- 分页、显式上限、超时和 schema 缺口必须进入完整性说明。

## SAPSkillhub 补证

- SAPBusinessAgents 只传递业务对象、字段、有界过滤、升序稳定键和 `max_rows`。
- Skill 自己管理连接、认证与受保护配置。
- 只有 `read_only=true`、`validated=true`、分页完整且 manifest 校验通过的结果才可关闭证据缺口。
- `partial`、`failed` 或达到上限只能作为有限证据。

## 人工 SE16N manifest

操作人员必须先确认 SAP GUI 会话的系统、client 和只读范围，再执行导出。manifest 至少记录：

- SAP system/client；
- 公司代码、会计年度、期间和检查 ID；
- 表名、行数、导出文件路径和 SHA-256；
- 复核人和复核时间；
- 标准化字段映射。

运行示例：

```powershell
month-end-closing --gateway se16n `
  --se16n-manifest .local/runs/1010-2026-07/se16n-manifest.json `
  --sap-client 100 `
  --company-code 1010 --year 2026 --period 7
```

任何 scope、client、字段、哈希或复核元数据不一致都会被拒绝。通用导出能力若不能施加可靠的公司代码和期间边界，则不得对 BKPF、BSEG、COEP 等大表执行无过滤读取。
