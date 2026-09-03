# 月结异常专项复核工作流

该模板先运行 `month-end-closing`，再以 `foreach`（并发 4、每类最多 50）处理范围完整的 GR/IR 与 AP 专项复核。

- 专项节点使用 `collect_inconclusive`；失败不会覆盖月结主结论。
- AA、外币估值、期间控制和 CO 异常不自动串联，只保留人工建议。
- `follow_up_scope_complete=false` 表示范围溢出或不完整，不得把已执行子集解释成完整复核。
- 当前模板保持 inactive，直到 `month-end-closing` 与 `ap-payment` 完成 Embedded OData 真实 SAP 验收。
