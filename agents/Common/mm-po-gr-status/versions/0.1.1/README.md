# 采购订单入库状态检查 / Purchase order receipt status check

业务域为MM-PUR / MM-IM，SAP业务组件覆盖采购订单与库存管理。本Agent按计划行交货日期执行严格只读的采购订单入库核对。计划行交货日期为唯一必填项；采购组织、采购组、工厂、供应商和采购订单号均为可选筛选，未填写时省略整条对应过滤。

The business domain is MM-PUR / MM-IM, spanning purchasing and inventory management. This Agent performs a strictly read-only purchase-order receipt check by schedule-line delivery date. The date is the sole required input; purchasing organization, purchasing group, plant, supplier, and purchase order are optional, and each corresponding filter is omitted in full when empty.

执行定义通过API_PURCHASEORDER_PROCESS_SRV读取采购订单计划行、项目和抬头，通过API_MATERIAL_DOCUMENT_SRV读取物料凭证项目和抬头；它不直接读取SAP表。系统用采购订单与项目组合键关联证据，随后按稳定键去重、计算收货与冲销净额，并按交货日期和计划行号分配到计划行。

The execution reads purchase-order schedule lines, items and headers through API_PURCHASEORDER_PROCESS_SRV, and material-document items and headers through API_MATERIAL_DOCUMENT_SRV; it does not read SAP tables directly. Evidence is joined by purchase order and item, deduplicated by stable keys, netted for receipts and reversals, and allocated to schedule lines by delivery date and schedule-line number.

结果按采购订单、项目和计划行展示计划数量、净收货数量、未收货数量、最新收货过账日、SAP交货完成标识及入库状态。只有采购订单单位与收货单位可直接比较时才形成数量结论；系统不执行单位换算。零匹配返回明确空结果，同键冲突、来源不完整或单位不可比较会保留为证据缺口或不确定结果。

Results show scheduled, net received and open quantities, the latest receipt posting date, SAP delivery-complete flag, and receipt status by purchase order, item, and schedule line. Quantity conclusions require directly comparable purchase-order and receipt units; no unit conversion is performed. Zero matches produce an explicit empty result, while conflicting keys, incomplete sources or incomparable units remain evidence gaps or inconclusive results.

本草稿为0.1.1资料修订，执行定义和托管规则保持0.1.0不变。它复用来源版本的PASS验收，不改写原SAP验收日期或比较结论，也不会执行SAP写操作。

This 0.1.1 draft is a documentation revision. Its execution and managed rule remain identical to 0.1.0. It reuses the source PASS acceptance without rewriting the original SAP acceptance date or comparisons, and never performs SAP writes.