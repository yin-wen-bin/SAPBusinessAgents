# Internal Order and Project Control Assistant 0.4.0: SAP data contract

## Object identity

- Internal-order input is trimmed, uppercased, and ALPHA padded before AUFK lookup. Exactly one row with `AUTYP=01`, the requested company code, object number, and controlling area is required.
- WBS input is trimmed but its case and separators are preserved. `sap-wbs-object-resolver` performs exact lookups against the fixed Project V2 and Financial WBS profiles, validates both metadata SHA-256 values, and requires both sources to agree on external/internal ID, object number, company code, controlling area, and project relationship.
- Both branches emit one shared `resolved_object`. Missing, duplicate, incomplete, or conflicting relationship evidence stops all downstream amount interpretation.

## Amount evidence

| Evidence | Source | Amount contract | Required dimensions |
|---|---|---|---|
| Actual | `A_OperationalAcctgDocItemCube` | `AmountInCompanyCodeCurrency` | resolved object, company, fiscal year, company-code currency |
| Plan | `A_FinPlanningEntryItem` | `AmountInCompanyCodeCurrency` | resolved object, company, fiscal year, planning category, ledger, currency |
| Budget | BPJA via `sap-adt-table-export` | `WTJHR` / `TWAER` | `OBJNR`, `GJAHR`, `WRTTP=41`, `VERSN=000`, explicit `LEDNR`, live stable key |
| Commitment | `sap-control-object-commitment-evidence` | signed finite Decimal groups | resolved object, fiscal year, every accounting period 1..16, value types 21/22/24/26, currency and currency role |

The commitment rule consumes only `commitment_details`, `commitment_totals.groups`, requested type scope, and explicit completeness flags from the dedicated Skill. All 21/22/24/26 types must be represented by evidence or an authoritative explicit zero. It does not read COOI rows, infer value types, discard invalid records, or substitute missing values with zero.

## Completeness

- `source_complete` is the conjunction of required object, actual, plan, budget, and commitment source/paging reads.
- `evidence_complete` additionally requires valid plan/actual/budget/commitment amounts, the versioned budget ledger, all requested commitment types, one company-code-currency role, the current object-mode acceptance gate, and no issue codes.
- An authoritative complete-empty commitment response may be zero only when the Skill supplies explicit zero details and currency context. An absent or unvalidated source remains `null`.
- A failed Skill, metadata mismatch, truncated page, incomplete period range, ambiguous plan category/ledger, invalid Decimal, or currency conflict makes the business result `inconclusive` and suppresses derived EAC.

All SAP operations are GET-only OData, approved read-only ADT Data Preview, or an approved semantic read-only SOAP query POST. The current commitment profile has no enabled endpoint. There is no SAP write, dynamic source discovery during formal execution, automatic SE16N fallback, currency conversion, or amount estimation.
