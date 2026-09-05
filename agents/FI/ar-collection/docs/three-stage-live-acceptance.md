# AR Collection 1.2.0 live acceptance

Status: `PASS / validated_pending_activation`

## Acceptance scope

Version 1.2.0 was validated with an independent direct-SAP baseline, an isolated candidate fixed-Agent snapshot, and the generic Chinese and English result pages. Free-query comparison was not required because this release uses the platform's deterministic-runtime acceptance mode.

All SAP business reads used GET-only OData. No dunning, clearing, posting, notification, or master-data write was executed.

## Live cases

### Actionable customer

- Input: `USCU_L09 / 1710 / 2026-09-06`.
- Candidate run: `acceptance_a72266d057f74661`.
- Direct baseline and candidate comparison: `MATCH`.
- Open items: 61.
- Action-required items: 60.
- Monitor-only items: 1.
- Ordinary overdue receivables: `1,189,917.00 USD`.
- All 60 actionable items are over 90 days overdue, have dunning level 0, and receive `initiate_first_dunning / high` under the SAPBusinessAgents processing-priority rule.
- `9400000001 / 2026 / 1`, due `2026-10-05`, is `monitor_until_due` and is excluded from the action worklist.
- The worklist CSV contains exactly 60 data rows and a UTF-8 BOM.

### Complete empty customer

- Input: customer `900000 / 1710 / 2026-09-06`.
- Candidate run: `acceptance_9236e4452f094479`.
- Direct baseline and candidate comparison: `MATCH`.
- Open, actionable, and monitor-only item counts are all zero.
- The result is `normal`, and the report explicitly states that no item currently requires immediate action.
- The worklist CSV exists and contains a header with zero data rows.

## Frontend and artifact result

- Chinese and English pages show the same headline, stage counts, action worklist, full open-item detail, reasons, and recommended actions.
- The first actionable item displays the reason and recommendation instead of only a customer-level attention status.
- Action categories are localized business labels; technical codes are not used as the main table value.
- Markdown, JSON presentation, and `ar-collection-worklist.csv` use the same deterministic rows.
- The generic platform presentation and artifact APIs were reused; no Agent-specific frontend endpoint or component was added.

## Automated coverage

Fixtures cover all aging bands, existing dunning, dunning blocks, special G/L, credit balances, evidence gaps, duplicate rows, conflicting dunning master data, and an empty worklist. Platform tests cover CSV formula-injection protection.

There are no remaining blocking limitations. Version 1.1.0 remains active until the lifecycle service atomically activates this validated candidate.
