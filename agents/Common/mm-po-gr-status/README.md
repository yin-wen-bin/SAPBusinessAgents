# 采购订单入库状态检查 / Purchase order receipt status check

按计划行交货日期执行严格只读的采购订单入库核对。计划行交货日期为唯一必填项；采购组织、采购组、工厂、供应商和采购订单号均为可选筛选，未填写时会省略整条对应过滤。

The Agent performs a strictly read-only purchase-order receipt check by schedule-line delivery date. The date is the sole required input; purchasing organization, purchasing group, plant, supplier, and purchase order are optional, and each corresponding filter is omitted in full when empty.

结果按采购订单、项目和计划行展示计划数量、净收货数量、未收货数量、最新收货过账日、SAP交货完成标识及入库状态。收货与冲销按物料凭证方向计算，且仅在单位可直接比较时形成数量结论。

Results show scheduled, net received and open quantities, the latest receipt posting date, SAP delivery-complete flag, and receipt status by purchase order, item, and schedule line. Receipts and reversals use material-document direction and quantities are concluded only when units are directly comparable.

本草稿尚未完成正式三级验收，也不会执行SAP写操作。

This draft has not completed formal three-stage acceptance and never performs SAP writes.
