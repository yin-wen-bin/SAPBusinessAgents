# Codex Harness prototype acceptance report

Tested at: 2026-08-19 (Asia/Shanghai)

## Scope and verdict

The free-query path was exercised through the Codex App Server Harness against the configured
live SAP system. Fixed Agents and published workflows were not redirected. Embedded SAP Read
Provider was the only OData execution channel, and extension evidence was restricted to approved
read-only skills. No host shell, file-editing tool, Computer Use, or SAP write operation was enabled.

Verdict: **PARTIAL**. The Harness proved the requested key-date supplier result and its crash
recovery contract. The business answer remains intentionally `INCONCLUSIVE` because FI clearing
postings do not independently prove bank settlement. The historical run's ADT call failed before
evidence creation; a separate post-fix bounded ADT acceptance completed successfully.

## Live result

- The target supplier/company/key-date query returned exactly six `K`-account supplier line
  items; `A`, `M`, `S`, and `WE` records were excluded from the conclusion.
- Five credit/payable items totalled USD 16,205.30 and one debit payment item was USD 5,000.00,
  producing a key-date net payable of USD 11,205.30.
- Complete, bounded-by-business-filter OData evidence was retained by reference; raw rows stayed
  under the ignored Harness evidence directory and were not copied into the public report.
- Subsequent FI clearing evidence was obtained. A complete payment-advice query returned zero
  rows. The report therefore confirms FI posting/clearing status but does not claim actual bank
  settlement.
- Every SAP HTTP request recorded by the provider was `GET`. Four successful SAP query calls
  produced evidence; failed query revisions did not become evidence.

## Harness behavior

- The App Server created a persistent thread and used iterative catalog, live metadata,
  validation, query execution, evidence inspection, and query revision calls.
- A separate live smoke run emitted two Native Web Search start/completion event pairs. Web
  results were used only for diagnostics and did not become customer-business evidence.
- The API process was stopped after evidence and final-report validation had been persisted. On
  restart, the same run and thread resumed and completed in its second turn.
- Tool-call count remained 27 before and after recovery. All `(tool, request_hash)` pairs were
  unique; no SAP query was replayed.
- A durable completed failure is replayed as the same observation. A call left in unknown running
  state returns `tool_call_recovery_unknown` and is not automatically repeated.

## ADT post-fix acceptance and remaining gap

The historical live ADT attempt encountered the pre-fix platform wrapper error that omitted the
Skill input validation capability. The wrapper and plugin operation were corrected and covered by
automated tests. A separate exact-filter `max_rows=2` live acceptance then returned
`status=complete`, `read_only=true`, `validated=true`, `source_complete=true`,
`paging_complete=true`, zero rows, and zero validation issues. The completed zero-row result is not
retroactively attached to the historical run, and it does not supply the independent
payment-execution or bank-statement evidence required to prove bank settlement.

## Automated verification

- Python: 220 passed; one upstream Starlette deprecation warning.
- Python bytecode compilation: passed.
- Site catalog validation: 30 Agents across 6 modules.
- Astro check: 0 errors, 0 warnings, 0 hints.
- Site tests: 18 passed.
- Static build: 74 pages.
- SAPSkillhub ADT smoke: complete, read-only, validated, paging complete, zero rows.
