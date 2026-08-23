# 30-Agent three-stage live SAP acceptance campaign

All direct baselines use `codex_app_direct_sap` and do not call SAPBusinessAgents. Embedded SAP execution is GET-only; approved supplemental ADT evidence may use the Skill's read-only Data Preview transport. No SAP write operation is allowed, and raw rows, URLs, and credentials remain in ignored local artifacts.

The `billing-block-diagnosis` row reflects the 2026-08-23 incremental VBUV revalidation. The `inventory-health-balancing` row reflects the 2026-08-23 full-history FIFO quantity-aging revalidation. All other rows retain their original campaign evidence.

## Summary

- PASS / executable: 17
- BLOCKED / disabled: 13
- FAIL / disabled: 0

## Agent results

| Module | Agent | Verdict | Executable | Free query | Fixed Agent | Evidence scope | Blocking limitation |
|---|---|---:|:---:|:---:|:---:|:---:|---|
| CO | `budget-rolling-forecast` | BLOCKED | false | MATCH | MATCH | bounded | budget_evidence_missing |
| CO | `co-month-end-allocation-settlement` | BLOCKED | false | MATCH | MATCH | bounded | allocation_cycle_evidence, object_status_evidence, settlement_rule_evidence |
| CO | `cost-center-expense-anomaly` | BLOCKED | false | MATCH | MATCH | bounded | plan_evidence_missing |
| CO | `internal-order-project-control` | BLOCKED | false | MATCH | MATCH | bounded | budget_evidence, commitment_evidence, control_object_not_found, master_evidence, plan_evidence |
| CO | `product-cost-variance` | BLOCKED | false | MATCH | MATCH | bounded | standard_cost_evidence |
| FI | `ap-payment` | PASS | true | MATCH | MATCH | complete | none |
| FI | `ar-collection` | PASS | true | MATCH | MATCH | complete | none |
| FI | `gr-ir-clearing` | PASS | true | MATCH | MATCH | complete | none |
| FI | `month-end-closing` | PASS | true | MATCH | MATCH | complete | none |
| MM | `intelligent-sourcing-rfq` | PASS | true | MATCH | MATCH | complete | none |
| MM | `inventory-health-balancing` | PASS | true | MATCH | MATCH | complete | none |
| MM | `material-shortage-procurement-response` | PASS | true | MATCH | MATCH | complete | none |
| MM | `procure-to-pay-status` | PASS | true | MATCH | MATCH | complete | none |
| MM | `supplier-performance-risk` | PASS | true | MATCH | MATCH | complete | none |
| PP | `demand-forecast-planning` | BLOCKED | false | MATCH | MATCH | bounded | pir_evidence, sales_demand_period_evidence |
| PP | `mrp-exception-analysis` | PASS | true | MATCH | MATCH | complete | none |
| PP | `production-order-monitoring` | PASS | true | MATCH | MATCH | complete | none |
| PP | `production-scheduling-capacity` | BLOCKED | false | MATCH | MATCH | bounded | complete_capacity_bucket_evidence |
| PP | `production-variance-analysis` | BLOCKED | false | MATCH | MATCH | bounded | production_cost_evidence, production_cost_relationship |
| SD | `billing-block-diagnosis` | PASS | true | MATCH | MATCH | complete | none |
| SD | `billing-completeness-check` | PASS | true | MATCH | MATCH | complete | none |
| SD | `billing-dispute-classification` | BLOCKED | false | MATCH | MATCH | bounded | billing_dispute_case_evidence |
| SD | `billing-output-monitor` | BLOCKED | false | MATCH | MATCH | bounded | billing_output_status_evidence |
| SD | `delivered-not-billed` | PASS | true | MATCH | MATCH | complete | none |
| SD | `delivery-delay-prediction` | PASS | true | MATCH | MATCH | complete | none |
| SD | `due-delivery-prioritization` | PASS | true | MATCH | MATCH | complete | none |
| SD | `order-to-cash-anomaly-monitor` | BLOCKED | false | MATCH | MATCH | bounded | billing_dispute_case_evidence, billing_output_status_evidence |
| SD | `order-to-cash-status` | PASS | true | MATCH | MATCH | complete | none |
| SD | `returns-credit-anomaly` | BLOCKED | false | MATCH | MATCH | bounded | return_receipt_evidence, return_refund_type_evidence |
| SD | `shortage-allocation-advisor` | BLOCKED | false | MATCH | MATCH | bounded | atp_availability_evidence |
