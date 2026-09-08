# Billing Block Diagnosis

诊断销售订单、项目与交货层的开票冻结、信用检查及不完整状态。

## 能力

- 严格只读，Embedded SAP Read Provider优先，SAPSkillhub仅按缺口补证。
- Embedded缺少项目级不完整状态时，条件调用 `sap-adt-table-export` 精确读取VBUV不完整日志；预检限定一个订单项目，正式查询最多200行。
- 支持Fixture和脱敏evidence输入，输出统一Markdown或JSON契约。
- 自然语言示例：为什么这张订单不能开票？

## 运行

```powershell
$env:PYTHONPATH = "src;.."
python -m billing_block_diagnosis "为什么这张订单不能开票？" --source fixture --as-of 2026-08-10 --json
```

真机证据由验证编排层写入被忽略的 `.local-data/live-tests/billing-block-unblock/<run-id>/`，再通过 `--source evidence --evidence <path>` 读取。Agent不会直接执行SAP写操作。

## 数据源

- `API_SALES_ORDER_SRV`
- `API_OUTBOUND_DELIVERY_SRV`
- `API_BILLING_DOCUMENT_SRV`
- 条件ADT对象：`VBUV`，仅选择 `VBELN`、`POSNR`、`ETENR`、`TBNAM`、`FDNAM`、`FEHGR`、`STATG`
- VBUV是缺失字段的稀疏日志；精确订单范围完整且返回零行表示未记录缺失字段，不要求每个订单项目各返回一行。

## 测试

```powershell
$env:PYTHONPATH = "src;.."
python -m pytest -q
```
