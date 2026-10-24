# Saved business-result display / 已保存业务结果展示

The shared, Provider-independent display projection uses the frozen execution snapshot's output Schema, not the current catalog or current draft. It runs only for saved deterministic Agent results and never writes execution, validation, acceptance, qualification or configuration records. Missing snapshots retain the original presentation without guessing a new Schema.

业务结论取自已保存的 `business_status`，标签取自冻结 Schema；额外公开的对象数组按已声明字段显示为表格，包括分币种金额。金额保留原字符串、符号及精度，不重算、不跨币种合计、不把 null 当作零。隐藏或未声明字段不加入展示。

Canonical-record tables are identified by their saved table title, column mapping, exact record set and row count. A declared enum with labels enables grouping. Group choices preserve unknown/unclassified values and count returned records only. Incomplete evidence remains incomplete. The existing GET table-page route accepts an optional `group` token; it pages the saved records, never SAP, and rejects groups not present in this projection. Old clients and non-projected results keep their original paging path.

本地规则步骤区分检查型输出与显式 `records` 输出：检查型记录数为不适用，不再因没有数据行误报“未找到”；业务记录只从一个明确结果集计数，不递归累加 `workflow_output`／`business_report` 中的副本。失败和不完整状态不得改成成功。历史步骤仅在确定性引擎的一一对应关系可确认时修正读取投影，原始记录不变。

The projection is a UI view, not new execution evidence or a new business PASS. All original authentication, authorization, Schema, business, completeness and acceptance checks remain authoritative. It neither invokes an SDK/model nor reads SAP. New runtime/model qualification is not awarded by a display repair.
