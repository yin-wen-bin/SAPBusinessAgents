# 创建并验证一个只读 Agent，以计划行交货日期 2026-07-17 查询采购订单入库情况；采购组织、采购组、工厂、供应商和采购订单号保持可选，未提供时不添加相应过滤。

# Agent detail (review draft)

Name: Purchase Order Schedule-Line Goods Receipt Status

Purpose: Query purchase-order receipt status by schedule-line delivery date and optionally narrow the scope by purchasing organization, purchasing group, plant, supplier, or purchase order. The stable output grain is purchase order + item + schedule line. This content does not mean that an Agent draft was generated, that any rule was implemented, or that the Agent was published or activated.

## Inputs

- Schedule-line delivery date: required and matched by exact date; the validation sample uses 2026-07-17.
- Purchasing organization: optional; when supplied, match A_PurchaseOrder.PurchasingOrganization.
- Purchasing group: optional; when supplied, match A_PurchaseOrder.PurchasingGroup.
- Plant: optional; when supplied, match A_PurchaseOrderItem.Plant.
- Supplier: optional; when supplied, match A_PurchaseOrder.Supplier.
- Purchase order: optional; when supplied, match A_PurchaseOrderScheduleLine.PurchasingDocument.
- If an optional input is absent, null, empty, or whitespace-only, the whole corresponding filter must be omitted. An empty or equals-null filter must never be generated.

## Fixed read-only steps

1. Read all matching schedule lines, scheduled quantities, and units from A_PurchaseOrderScheduleLine for the exact delivery date.
2. Bind purchase order and item together to A_PurchaseOrderItem and read plant, material, order quantity, goods-receipt expectation, deletion indicator, and SAP delivery-complete flag.
3. Bind A_PurchaseOrder to obtain purchasing organization, purchasing group, supplier, and order date.
4. Bind purchase order and item together to A_MaterialDocumentItem and read all related movements, quantities and units, debit/credit direction, cancellation fields, and reversal keys.
5. Bind material-document year and document number together to A_MaterialDocumentHeader and read posting date, document date, and creation timestamp.

Every SAP step must remain GET-only. The Agent must not create, change, post, approve, or delete SAP data.

## Validation sample result

For the complete scope with delivery date 2026-07-17 and all optional filters omitted, two schedule lines were found:

- Purchase order 4500001918, item 10, schedule line 1: scheduled quantity 1 PC, goods receipt expected, and SAP delivery-complete flag false. No linked document was present in the complete movement evidence, so the sample classification is Not received.
- Purchase order 4500001919, item 10, schedule line 1: scheduled quantity 1 PC. One uncancelled 101/S movement for 1 PC was present, SAP delivery-complete was true, and the latest goods-receipt posting date was 2026-06-18; the sample classification is Fully received.

The sample summary is two schedule lines, one fully received, one not received, and a 50% fully received rate.

## Evidence provenance

- ev_0254d5d2d85a910b35197e1c: two schedule lines.
- ev_59e33f50f5cbc2ee8700266b: two purchase-order items.
- ev_ef89082e65e95e943f3325c2: two purchase-order headers.
- ev_5840743144e6c5ab74010da6: one material-document item.
- ev_01ca58e4e3044fc34dcd5477: one material-document header and its dates.

All references are run-scoped sap_live evidence and report source_complete=true. This supports absence reasoning only within this exact sample scope; it does not establish results for other dates, other filter combinations, or future executions.

## Completeness limitations

The sample validates only the 2026-07-17 delivery-date path with all five optional inputs omitted. It does not establish runtime behavior when optional inputs are supplied individually or in combination. Every later run must obtain fresh, complete, untruncated, unbounded-by-top evidence with paging finished. If any source is incomplete, a binding key is missing, or quantity units are not comparable, receipt quantity and status must remain unknown rather than being reported as zero.
