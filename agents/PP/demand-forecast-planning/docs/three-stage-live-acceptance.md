# Three-stage live SAP acceptance: demand-forecast-planning

## Verdict

`BLOCKED` / `executable=false`

- Case: `demand-forecast-planning-live-001`
- Tested at: `2026-08-20T09:12:01.093603+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `pir_evidence, sales_demand_period_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:5dc4d2ff08665bb5b70399f6154f268e8c990775da33d0c558b33ef46519e2ee`
- SAPBusinessAgents free query: `sha256:44d93a68bbc1dcc6f8d961b1a81c77fae07c94bfd79605c98e02bb29256f13f1`
- Adjudicated result: `sha256:5dc4d2ff08665bb5b70399f6154f268e8c990775da33d0c558b33ef46519e2ee`
- Fixed Agent: `sha256:eea9435c4644306b463d16319ceb3e37c98f414b28f51b5c71effe4ecbae74f6`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, material, plant` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, material, plant` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, requirement_date`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| forecast_sales | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 527 | 1 | SalesOrder, SalesOrderItem | true | true |
| forecast_planned | API_PLANNED_ORDERS | 2.0 | A_PlannedOrder | 0 | 1 | PlannedOrder | true | true |

Schema/query manifests:
- `forecast_sales` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:2ea804adce5468631a425c076ed2d02e8f542e8208999af6605767ad61be2a45`.
- `forecast_planned` schema `sha256:379cf75a62ac739739e9764a7862f3168912cf01fc081b0b7db790b7ddde0dd0`; query `sha256:5ec26848324d2e313867372fef1649bf54c5e28791fb9ea974d5fdfadcc84069`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
