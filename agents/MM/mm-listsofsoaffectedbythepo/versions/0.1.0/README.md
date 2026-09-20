# free-query-e5e65aec

Generated from a validated free SAP query.

- Query: 如果采购订单4500000114不能按时到货，会影响哪些销售订单？
- Prior correction: Remove the separate PurchasingDocument, MRPPlant, and ProductionPlant filter inputs; reuse purchase-order values and derive plant scope from purchase-order item evidence.
- Current input revision: Purchase order is required and accepts multiple values; material and sales order are optional single-value scope refinements, and the sales-order control uses the same one-line height as material.
- Boundary: selected SAP Provider GET-only execution

Review the business semantics, input schema, output contract, completeness requirements, and deterministic rules before publishing.

## 候选筛查与验收 / Candidate screening and acceptance

按采购订单实际物料与工厂配对、销售订单类MRP需求及订单项目筛查潜在关联候选，不计算实际短缺或交期推迟。

Screen potential-association candidates only, not causal delay impact, shortage quantity or delivery changes. Start from the confirmed purchase_order's items, narrowed by optional material. Preserve each actual Material/Plant pair; never form a Cartesian product. For those exact pairs use sales-order demand (MRPElementCategory VC); optional sales_order restricts MRPElement. Include a sales order only when at least one sales-order item has that same order, material and production plant in the corresponding MRP demand. Deduplicate verified items by SalesOrder/SalesOrderItem and schedule lines by SalesOrder/SalesOrderItem/ScheduleLine; count only schedule lines of verified items. Read complete evidence before claiming exhaustive results. Complete empty scope is zero; missing, bounded or conflicting required evidence is inconclusive. No stock allocation, FIFO, date cutoff, unit conversion, ATP or pegging is performed. Counts in incomplete output describe observed verified records only.

规范记录和业务状态见 execution.acceptance；三级比较仅检验本次案例，不证明实际延期因果关系。
Canonical records and business states are defined by execution.acceptance; case acceptance does not establish causal delay impact.

<!-- generated:facts:start -->
<!-- generated:facts:end -->
