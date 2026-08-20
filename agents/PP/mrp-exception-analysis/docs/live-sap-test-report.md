# 真实 SAP 测试报告：MRP 异常分析

- 结论：**INCONCLUSIVE / PARTIAL PASS**。MRP 主数据成功，覆盖和供需明细在真机端超时，不能断言“无短缺”。
- 历史只读基线：物料 `SG21`、工厂/MRP 范围 `1010` 返回 MRP 类型 `PD`、控制员 `001`、策略组 `40`（case `89c828ce-a459-4572-b5ca-b4c5b5a43fc5`）。
- 规则验证：不带参数文件的 MaterialCoverages 返回 400“必须完全指定短缺参数文件和短缺计数器”（case `511fe9a8-6a4e-4572-83d7-50c9c4f690c9`）。
- 性能验证：使用标准 `SAP000000001/001`、精确物料和完整键后，MaterialCoverages 仍在约 36 秒超时（case `3c4da4b8-580b-4925-bfea-8a20a193189e`）；SupplyDemandItems 也在约 32 秒超时（case `9b824ced-a8d8-4bb1-a19e-ce482d2a0fc2`）。
- SAPSkillhub：SE16N 成功导出 `MDKP` 10 行，证明 MRP 清单数据存在；未完成 MDKP 到 OData 供需项目的同键闭环。
- 限制：超时属于 SAP 服务/运行性能结果；Embedded Provider 正确返回失败，没有伪造空结果。
- 安全：未运行 MRP，未修改计划订单或物料主数据。
