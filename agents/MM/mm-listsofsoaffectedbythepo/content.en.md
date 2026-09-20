# 如果采购订单4500000114不能按时到货，会影响哪些销售订单？

## Agent detail

**Name:** Sales-order impact analysis for a delayed purchase order

**Purpose:** For a specified purchase order, follow the evidence chain “purchase order → material/plant → MRP supply and demand → sales-order items and schedule lines” to identify directly assigned sales orders and orders potentially exposed through a shared stock pool.

**Input:** One or more purchase orders are required; optional single-value material and sales-order inputs only narrow the complete evidence chain, while plant remains derived from purchase-order item evidence. The source run used purchase order `4500000114` and obtained material `MZ-TG-Y240` and plant `1710` from SAP evidence.

**Fixed read-only steps:**
1. Query the exact purchase-order account assignments for sales-order, item, and schedule-line references.
2. Read the exact purchase-order item and schedule line for material, plant, quantity, unit, scheduled receipt date, and delivery status.
3. Read the complete MRP supply-demand set for the derived material and plant.
4. Use the complete MRP subset with element category `VC` as the sales-demand candidates in the same planning scope.
5. Verify the candidate sales-order items using the order numbers together with the exact material and plant.
6. Read all schedule lines for those orders and retain requested dates, confirmed dates, confirmed quantities, and delivered quantities.

**Conclusion supported by this evidence:** The purchase order has one active item for `796 PC` of material `MZ-TG-Y240` at plant `1710`; its scheduled delivery date is `2018-01-18`, and the item is not completely delivered. The complete account-assignment query returned zero rows, so there is no evidence of a direct assignment to a sales order. The shared supply-demand scope contains nine potentially exposed sales orders: `5706, 5707, 5708, 5750, 5751, 5752, 5794, 5795, 5796`. They comprise 18 items and `939 PC` of demand. All 18 schedule lines have zero ATP-confirmed quantity and no confirmed delivery date.

In a quantity-only scenario, removing the purchase order’s entire `796 PC` from the complete cumulative MRP availability leaves a minimum balance of `71 PC`. The evidence therefore does not prove that any listed order must become short solely because this purchase order is delayed. The nine orders should be classified as “potentially exposed, pending ATP/pegging review,” not as confirmed impacts.

**Evidence provenance:** Source run `run_820479b4b01b4add`; all seven sources are `sap_live`, scoped as `customer_business_fact`, and report `source_complete=true`. Direct assignments: `ev_b106fb7150c5cd0ca069fc1e` (0 rows); PO schedule line: `ev_d653db6e9b17c4306a54239d` (1 row); PO item: `ev_6b04f56d00e9f0e8b6827820` (1 row); complete MRP situation: `ev_941f4dca185e3ed776080330` (66 rows); sales-demand subset: `ev_f26df70da1f4a901eebfd349` (18 rows); sales-order items: `ev_534a88de6e6931cceecc9e93` (18 rows); sales-order schedule lines: `ev_456129f9d780586d5be72637` (18 rows).

**Read-only boundary:** Every SAP operation must remain GET-only. The Agent must not change the purchase order, rerun or update ATP/backorder processing, create deliveries, post goods receipts, or modify sales orders.

**Completeness limitations:** The supplied evidence is complete and untruncated for the defined query scope, supporting a complete direct-assignment result and potential-exposure set. `source_complete=true` does not establish direct supply-demand pegging and is not a simulation of ATP after the delay. The final affected subset still depends on the revised expected receipt date, ATP/backorder priorities, and whether other stock or supply changes.
