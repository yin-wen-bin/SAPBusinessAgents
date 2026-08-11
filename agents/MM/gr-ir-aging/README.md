# GR/IR Ageing Analysis Agent

这个 MM/FI 纵向切片通过 Thin SAPClaw 的只读 `A_OperationalAcctgDocItemCube` 证据，按采购订单项目计算 GR/IR 未结余额、最后活动日和账龄。它不自动化 MB5S，也不写入 SAP。

## 运行

```powershell
$env:PYTHONPATH = "src"
python -m gr_ir_aging --company-code 1000 --key-date 2026-08-01 --json
```

使用编排层生成的真实 evidence：

```powershell
python -m gr_ir_aging `
  --company-code 1000 `
  --key-date 2026-08-01 `
  --evidence D:\runs\gr-ir-evidence.json `
  --purchase-orders 4500001000,4500001001 `
  --plants 1000 `
  --gl-accounts 22010000 `
  --ageing-threshold 30 `
  --ageing-buckets 30,60,90 `
  --json
```

Evidence 必须声明 `completeness.complete=true`、`source_complete=true`、`metadata.read_only=true`，并完整包含会计凭证项目。缺页、重复业务键、混合公司代码币种或普通 PO 的不可换算单位会使结果成为 `inconclusive`，且不输出余额。

## 规则边界

- GR/IR 归类优先使用 `GrIrSourceType`，再使用原始参考凭证类型和会计凭证类型。
- 借贷标识和冲销标识共同决定金额与数量方向；余额为 PO 项目内 GR/IR 会计行的有符号净额。
- 混合交易币种只警告，最终余额始终使用公司代码币种；混合公司代码币种直接拒绝。
- 服务采购订单可以没有统一数量单位，金额结论仍可成立；非服务采购订单不能静默换算单位。
- 所有查询均须通过 Thin SAPClaw 完整分页。当前不可执行的 CDS-only PO history 视图不是生产依赖。

## 测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

字段、分页和证据契约见 [docs/sap-data-contract.md](docs/sap-data-contract.md)。
