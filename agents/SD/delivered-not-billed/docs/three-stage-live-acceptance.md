# Three-stage live SAP acceptance: delivered-not-billed 0.2.0

## Verdict

`PASS` / `executable=true`

- Tested at: `2026-09-06T15:25:40+08:00`
- Acceptance mode: `deterministic_runtime`
- Embedded Provider: `embedded-odata` `2.0.0`, read-only
- Fixed-Agent comparison: `MATCH`
- Free-query comparison: `NOT_TESTED`（确定性Agent本轮不以自由查询作为验收门槛）
- Normalized delivery-item records: `18`
- SAP write operations: none

## Three stages

1. 实时Schema与计划验证：7个计划步骤全部通过字段、GET-only和批准关系校验。实时元数据不宣称交货键可排序，因此两个交货步骤不发送不受支持的`$orderby`；本次均为单页完整结果，规则层按交货号和项目号确定性排序。
2. Embedded直接GET基线：查询源完整，无截断、无校验问题；交货抬头18行、交货项目18行、开票项目18行、开票抬头18行、取消反查0行、销售订单项目18行。
3. 固定Agent：执行6次真实GET，返回18条项目记录；`source_complete=true`、`business_complete=true`，与直接基线的规范化结果Hash一致。

## Business result

- `delivered_not_billed=0`
- `unbilled_items=0`
- `partially_billed_items=0`
- `fully_billed_items=18`
- `overbilled_items=0`
- `inconclusive_items=0`
- 业务状态：`normal`
- 数量按1种单位独立汇总，金额按1种币种独立汇总；未跨单位或币种合计。

本次真实样本只观测到完全开票状态。完全未开票、部分开票、超量开票以及截止日前取消状态由Fixture覆盖，不标记为真机状态通过。

## Evidence hashes

- Embedded direct baseline: `sha256:bd94ddd7e3250601d686be0a5be277500d874e38a2da2a838179e1dc2a98bc0a`
- Fixed Agent: `sha256:bd94ddd7e3250601d686be0a5be277500d874e38a2da2a838179e1dc2a98bc0a`
- Candidate execution: `sha256:68f61927c2f098527e367a66a3a1afcc4dab2db67b1a79c0822aff0764510372`

原始SAP行、业务标识、金额、URL和凭据仅保存在被忽略的`.local-data/live-tests/embedded-adt/`中；仓库报告只保留聚合证据。
