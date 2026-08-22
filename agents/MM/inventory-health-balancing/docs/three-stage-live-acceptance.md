# Three-stage live SAP acceptance: inventory-health-balancing

## Verdict

`PASS` / `executable=true`

- Agent version: `0.2.0`
- Tested at: `2026-08-23T01:03:51.11039+08:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: none
- SAP write operations: none; every SAP request was `GET`

## Accepted live scope

The live target was material `TG10`, plant `1710`, storage location `171A`.
All three scenarios returned current unrestricted-use stock of `7,805 PC` for
stock type `01` and blank special stock.

| Scenario | Enabled checks | Direct baseline | Free query | Fixed Agent | Business result |
|---|---|:---:|:---:|:---:|---|
| Snapshot only | none | PASS | `run_16ec773e21184677` / MATCH | `acceptance_5cc673233859431a` / MATCH | `snapshot_only`; all three checks `not_requested` |
| Obsolete + expiry | obsolete 365 days; expiry 90 days | PASS | `run_0fe7ebd86d6240d1` / MATCH | `acceptance_5d38ed2a79174efc` / MATCH | obsolete `candidate`; expiry `not_candidate`; slow-moving `not_requested` |
| All checks | slow-moving 180 days; obsolete 365 days; expiry 90 days | PASS | `run_1bb1063b81fa4097` / MATCH | `acceptance_970c98d52a694dbb` / MATCH | slow-moving and obsolete `candidate`; expiry `not_candidate` |

The complete 365-day movement-item query returned zero rows. Therefore no last
movement date was invented: `last_movement_date=null`, `stock_age_days=null`, and
`stock_age_lower_bound_days=365`. The current positive stock has no batch, so no
positive-stock batch qualified for the 90-day expiry window.

## Full-check evidence hashes

- Codex direct baseline: `sha256:80611fb622b68ee0e607fd35365a68a5af4d05400a7cbcf7a3ac833a4a488928`
- SAPBusinessAgents free query: `sha256:85eba94ceba6fe4b7518f3a5971de90e44319e97a188b93655544da252a9882f`
- Adjudicated result: `sha256:80611fb622b68ee0e607fd35365a68a5af4d05400a7cbcf7a3ac833a4a488928`
- Fixed Agent: `sha256:58a7852b49b60766413ba24171a1744848c376227306d11fd69aecc6a4737d37`
- Fixed comparison: `sha256:0ddbb15636d66f3d1f75a41aead36319b061d6c555c1208fe84c28c98e360950`

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|:---:|:---:|
| current stock | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 1 | 1 | true | true |
| movement items | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 0 | 1 | true | true |

The header step uses grouped `MaterialDocumentYear + MaterialDocument`
`filter_from_previous` bindings. Because the exact movement-item step returned
zero rows, the provider correctly returned an empty dependent header result and
did not issue a broad header query.

## Removed scope

This version does not reconstruct historical stock balances, call MB5B, subtract
safety stock, calculate transfer quantities, or recommend stock transfers. Raw
SAP rows, URLs, credentials, and connection details remain only in ignored local
acceptance artifacts.
