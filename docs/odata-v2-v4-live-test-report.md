# Embedded OData V2/V4 live acceptance report

- Tested at: `2026-08-19` (Asia/Shanghai)
- Provider: `embedded-odata`
- Capability: `sap_read.v2`
- Read boundary: GET-only
- Registered services: 52
- Supported protocol adapters: OData `2.0`, `4.0`

No SAP base URL, client, credential, response row, or customer document value is included
in this report.

## OData V2 — PASS

The approved registry entry for `API_MATERIAL_STOCK_SRV` was tested against live
`$metadata` with declared `odata_version=2.0` and entity set `A_MaterialStock`.

- live metadata protocol matched the plan and registry;
- the entity was present and executable;
- two live fields were returned without metadata truncation;
- the schema response declared `schema_authority=true`;
- a separate bounded `top=1` data request used only GET and returned one row;
- no raw row was logged or copied into this report.

The bounded data request correctly returned `source_complete=false`. It proves that the V2
adapter and registered live service can execute a GET; it does not claim that the entity's
source data was fully read.

## OData V4 — BLOCKED

The reviewed registry contains no approved OData V4 binding. No V4 path was inferred from
a service name, no arbitrary `/sap/opu/odata4/` URL was tried, and no live V4 PASS is
claimed.

Automated contract tests cover EDMX 4.0, `value`, `@odata.nextLink`, `contains`, date/time/
GUID literals, unbound GET FunctionImport execution, version mismatch, Action rejection,
and same-origin/exact-service-root paging. Live V4 acceptance remains blocked until an
administrator adds and reviews a real V4 service binding in `config/odata-services.json`.

## Verdict

| Scope | Verdict | Evidence scope |
|---|---|---|
| Embedded Provider configuration | PASS | complete configuration check |
| Live OData V2 metadata | PASS | complete requested metadata scope |
| Live OData V2 data request | PASS | bounded adapter proof; not source-complete |
| Automated OData V4 contract | PASS | simulated protocol contract |
| Live OData V4 service | BLOCKED | no approved V4 registry binding |

Overall acceptance is `PARTIAL`: V2 is live-validated and V4 behavior is automated, while
V4 live validation is truthfully blocked by the absence of an approved target-system
service binding.
