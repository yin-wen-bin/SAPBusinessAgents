# Three-stage live acceptance — month-end-closing v0.2.1

## Verdict

`PASS` / `executable=true`

The Agent execution and reporting contract passed three-stage live acceptance on 2026-09-08. This verdict means the independent direct-SAP baseline, a Codex App Server free query, and the fixed Agent produced the same canonical result. It does **not** mean that company code 1010 is ready to close, that a close was executed, or that an authorized approver approved a close.

The tested business result remains `inconclusive`: eight checks passed, three require attention, and one could not be assessed because no reviewed FX valuation run-status source is available.

## Accepted scope

- SAP client: `100`
- Company code: `1010`
- Fiscal year / period: `2026 / 9`
- As-of date: `2026-09-08`
- Company-code currency: `EUR`
- Fiscal-year variant: `K4`
- Controlling area: `A000`
- Leading ledger: `0L`
- Profile: `test-default-1010` version `1.0.3`
- Profile digest: `sha256:41713747053ad568c0ff86163231066ade6e4e78d57869f311463980bd36425a`

The account, document-type, threshold, status-map, and cost-center values in this profile are business-confirmed test values. They were not inferred from SAP master data.

## Canonical result

| No. | Check | Result | Live finding |
|---:|---|---|---|
| 1 | AP overdue | `attention` | 8 supplier items were open and past SAP `NetDueDate`; the free-query report showed a signed company-code amount of `-5444.76 EUR`. |
| 2 | AR unapplied receipts | `passed` | The configured DZ/bank-account seed and downstream customer chain were complete and empty. |
| 3 | GL unreconciled items | `passed` | The configured account scope returned a complete zero result. |
| 4 | Aged GR/IR | `passed` | The exact GR/IR seed was complete and empty, so both configured age buckets were zero. |
| 5 | GR/IR adjustment candidates | `passed` | The complete empty GR/IR seed established zero candidates; MR11 was not executed. |
| 6 | AA depreciation | `attention` | TABA did not prove depreciation complete through 2026 period 9; the available rows were historical. |
| 7 | FX valuation | `not_assessed` | `fx_valuation_run_status_source_unavailable` was retained as a required non-blocking limitation. |
| 8 | Automatic clearing | `passed` | The configured residual-item scope returned a complete zero result. |
| 9 | FI posting period | `passed` | T001 resolved posting-period variant 1010 and T001B showed the target period open; as-of date was before the period end, so this was not a closing issue. |
| 10 | MM period | `attention` | MARV returned a complete zero result and therefore did not prove that MM had advanced beyond the target period. |
| 11 | CO unallocated costs | `passed` | The exact A000/10101101 period scope returned a complete zero result. |
| 12 | SD billing transfer | `passed` | The configured transfer-status and posting-status B/C queries were complete and empty. |

Canonical metrics: `checks_total=12`, `checks_passed=8`, `checks_attention=3`, `checks_not_assessed=1`, `checks_error=0`. `source_complete=true`, while `checklist_complete=false`, `evidence_complete=false`, and `business_complete=false` because the FX check remains unassessed.

## Live source coverage

The independent baseline read ten sources and retained raw business rows only in encrypted, Git-ignored acceptance artifacts:

| Source | Access | Rows | Complete |
|---|---|---:|---|
| API_COMPANYCODE_SRV / A_CompanyCode | OData GET | 1 | yes |
| API_LEDGER_SRV / A_Ledger | OData GET | 5 | yes |
| API_GLACCOUNTLINEITEM / GLAccountLineItem, period scope | OData GET | 0 | yes |
| API_OPLACCTGDOCITEMCUBE_SRV / A_OperationalAcctgDocItemCube, supplier scope | OData GET | 11 | yes |
| API_BILLING_DOCUMENT_SRV / A_BillingDocument | OData GET | 0 | yes |
| API_GLACCOUNTLINEITEM / GLAccountLineItem, GR/IR seed | OData GET | 0 | yes |
| TABA | reviewed ADT data preview | 2 | yes |
| T001 | reviewed ADT data preview | 1 | yes |
| T001B | reviewed ADT data preview | 7 | yes |
| MARV | reviewed ADT data preview | 0 | yes |

The operational accounting-document cube is queried at company-code scope without a ledger predicate. Adding `Ledger='0L'` incorrectly suppressed the supplier rows on this system and was removed from both the independent baseline and fixed Agent plan. SAP-computed `NetDueDate` is used; the Agent does not derive due dates locally.

## Three-stage reconciliation

- Direct baseline hash: `sha256:3eaf628494f782a595ec649aab0a2c77b92dc88d21dfe6b5c10434bce19615b0`
- Free query: `run_68270507cf534dbe`, comparison `MATCH`, result hash `sha256:71e83d1168eba44e7490fec2d15d54c0cebba12b0498fad105ccb14881396f72`
- Fixed Agent: `acceptance_24673c7310a647cc`, comparison `MATCH`, result hash `sha256:fa1c444452ae524b1f7963b82037ad6ddaa3b9ed974c4b2dc4c3d48db2255912`
- Fixed-Agent comparison hash: `sha256:7814bf9ed688d1e50fdf3ba91154998521df3053b08255c1b96b232e3e53852e`

The hashes differ because the three runtimes retain different non-business fields and evidence references. Business-semantic comparison at `[company_code, fiscal_year, period, as_of, ledger]` returned no differences.

## SAP GUI and standard-program reconciliation

- A read-only SE16N export of T001B returned seven rows and matched the ADT T001B evidence used for FI-period assessment.
- F.05 / SAPF100 was opened only for a safe precheck. The program rejected New G/L processing and directed use of FAGL_FC_VAL. No valuation or posting run was started.
- FAGL_FC_VALUATION was not executed because a unique valuation area was not business-confirmed; T033 exposed multiple candidates. The Agent therefore keeps FX valuation as `not_assessed` instead of guessing.
- MR11 simulation was not executed because the complete GR/IR seed contained no candidate purchase-order scope, and no tolerance or variant was inferred.

## Safety boundary

- Business OData access is GET-only with schema validation, stable order, paging, and bounded date scopes.
- TABA, T001, T001B, and MARV are read only through the reviewed ADT data-preview skill. Restricted raw rows are not returned to the Runtime; only privacy-safe derived AA/FI/MM status is exposed.
- The Agent never executes or approves OB52, MMPV, AFAB, F.05, F.13, MR11, settlement, posting, or clearing.
- Empty results pass only when the exact source is complete and the check declares complete-zero semantics.
- Any schema drift, truncation, incomplete paging, profile mismatch, or missing required source fails closed.

The remaining FX limitation is non-blocking for Agent executability because the Agent reports it explicitly and conservatively. It remains blocking for a definitive company month-end readiness conclusion.
