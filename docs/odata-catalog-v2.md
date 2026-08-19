# OData Catalog v2 and offline catalog sources

## Authority and execution boundary

SAPBusinessAgents uses the in-process Embedded Provider as its only OData runtime. Every
request is `GET`; OData V4 Actions and every write method are rejected. The three catalog
sources have deliberately different authority:

1. `data/catalog-seed/catalog.json` is advisory search and planning material.
2. `config/odata-services.json` is the reviewed internal binding from
   `(service_name, odata_version)` to a relative service root and `$metadata` path.
3. Live `$metadata` is the only executable schema authority. A Seed entry never authorizes
   an entity, field, function, relationship, or protocol version.

The public catalog exposes artifact and protocol metadata but not service paths, SAP URLs,
clients, credentials, or headers. A plan must declare `odata_version` as exactly `2.0` or
`4.0` beside every `service_name`/`entity_set`. Missing and conflicting versions fail with
`odata_version_required` and `odata_version_mismatch` before a data query.

The four independent versions are:

- `odata_version`: wire protocol `2.0` or `4.0`;
- `artifact_version`: SAP Business Accelerator Hub release;
- `openapi_version`: OpenAPI or Swagger document version;
- `_0001`/`_0002` in a technical service name: part of the name, never a protocol hint.

## One-time sanitized snapshot import

The migration tool is intentionally outside `src/sap_business_agents_platform`; the runtime
does not import it or read the source workspace.

```powershell
.\.venv\Scripts\python.exe scripts\import_sapclaw_catalog.py `
  --source-root <snapshot-root> `
  --repository-root . `
  --dry-run
```

Remove `--dry-run` only after reviewing the summary. The importer consumes structured
service/entity/field/business-term indexes plus selected guidance. It removes transport
data and write guidance, quarantines inferred graphs/relations/lookup paths, and excludes
cases, responses, raw metadata, vectors, runtime code, frontends, MCP and Skills. It emits:

- the normalized Catalog Seed;
- the reviewed service registry candidate;
- an included/transformed/quarantined/rejected, privacy, quality and license report;
- an adjacent SHA-256 manifest. Each consumed source file is also hashed in the report.

No source workspace path is persisted in these outputs. The importer is for the migration
only; future product updates use the BAH flow below.

## SAP Business Accelerator Hub review flow

The repository-local administrator flow is the durable implementation. The Codex
`sap-bah-odata` Skill may invoke or inform this flow, but it is not registered in
`config/skills.json` and is never part of an Agent execution.

```powershell
$env:SAP_BAH_ID = "<administrator account>"
$env:SAP_BAH_PW = "<secret>"
.\scripts\Fetch-SapBahCatalog.ps1 -Artifact <artifact-id>

.\.venv\Scripts\python.exe scripts\sync_sap_bah_catalog.py `
  --artifact <artifact-id> `
  --service-name <technical-service-name>
```

Credentials are environment-only. The fetcher pins its Playwright CLI package, retries
bounded operations, clears browser state in `finally`, and writes raw JSON/YAML/EDMX under
ignored `.artifacts/sap-bah/`. The normalizer accepts OpenAPI 3, Swagger 2 and EDMX version
evidence, emits only GET operations, hashes the candidate and prints a registry diff.
If artifact evidence cannot establish the protocol, an administrator must explicitly pass
`--odata-version`; the service name is never used to infer it.

The lifecycle is fetch, normalize, diff, human review, then publish the sanitized Seed.
Breaking changes never overwrite the registry automatically. At execution time the target
system's live `$metadata` still filters unavailable entities and fields.

## Protocol adapters and completeness

The shared security kernel owns credentials, live schema checks, result ceilings, stable
ordering, completeness, audit redaction, and same-origin/same-service next-link validation.
The V2 adapter handles `d.results`, `__next`, `substringof`, V2 typed literals and GET
Function Imports. The V4 adapter handles EDMX 4.0, `value`, `@odata.nextLink`, `contains`,
V4 date/time/GUID literals and unbound GET Functions/FunctionImports. Bound functions are
currently rejected because the plan contract does not carry an approved binding-resource
identity.

An explicit `$top`, result ceiling, missing stable paging key, rejected next-link, or any
incomplete page keeps the evidence `INCONCLUSIVE`; a bounded result is never reported as
source-complete.

---

## 中文摘要

- Embedded Provider 是唯一 OData 执行链，严格 GET-only；V4 Action 和所有写方法均拒绝。
- Catalog Seed 只负责检索和规划，`odata-services.json` 只保存审核后的内部相对路径绑定，
  实时 `$metadata` 才是目标系统可执行 Schema 的唯一权威。
- 每个计划步骤必须显式携带 `service_name + odata_version + entity_set`；服务名中的
  `_0001/_0002` 不代表 OData 协议版本。
- 一次性快照导入只保留清洁后的结构化索引和只读业务语义；relations、graph、lookup
  path 全部隔离，原始响应、URL、client、凭据、旧 runtime/MCP/Skill 不迁移。
- 后续更新走 BAH 管理员离线流程：抓取、规范化、差异报告、人工审核、提交 Seed；原始
  规格仅保存在被忽略的 `.artifacts/sap-bah/`。
- 分页、结果上限、稳定键或版本证据不完整时保持 `INCONCLUSIVE`，不得把有界结果解释为
  全部源数据。
