# internal-order-project-control 0.4.0 真机测试报告

- 测试时间：2026-08-27
- SAP 写操作：0
- 结论：**BLOCKED / executable=false**

## 本轮实现与真机结果

- 新建 `sap-wbs-object-resolver`：输入仅允许 WBS 外部编号和公司代码；固定查询 Project V2 与 Financial WBS，不接受连接、URL、SQL、数据源或字段注入。
- 目标系统两个 OData 元数据指纹均与版本化 profile 一致。格式化 WBS 样本的直接基线、独立 Skill 和 SAPBusinessAgents `SkillRegistry` 执行均得到两个源各 1 行，外部编号、内部编号、对象号、公司代码、控制范围和项目内部关系六项检查全部一致，结果为 `complete/resolved/validated=true`。
- `SkillRegistry` 已将 resolver 作为可执行只读 Skill 开放，并确认未知 `source` 参数被严格 Schema 拒绝；通用 gap token 继续绑定运行号、Skill ID 和精确输入，专项回归覆盖输入变化、跨 Skill 和复用。
- 新建 `sap-control-object-commitment-evidence`：实现严格期间、值类型映射、Decimal、分页/范围、显式零、多币种分组和安全 SOAP 边界，但目标 profile 保持 `validated=false`。
- 固定 Agent 升级到确定性规则 v4：WBS 走 resolver，内部订单走 AUFK，两种对象统一调用 commitment Skill；规则输出21/22/24/26分类状态、预算账本、币种角色、比较币种和独立模式验收状态。
- 已从 Agent 清单和执行步骤删除 PRPS/COOI ADT 投影。BPJA 预算读取保持不变。

## 目标系统承诺能力矩阵

| 候选 | 结论 | 原因 |
|---|---|---|
| Project Commitment SOAP | 未开放 | SAP 官方契约证明期间化 WBS 承诺语义，但目标 `/sap/bc/srt/wsil` 未暴露可调用 Project Commitment binding。 |
| 内部订单 COSP/COSS | 部分证明但未达门禁 | COSP 键查询稳定找到值类型22、账本00和CNY记录，COSS完整返回0行；期间金额列反复出现 `Unknown column VERS`，尚未和权威基线对账。 |
| COOI | 禁用 | 已探测的金额字段仍被 ADT Data Preview 拒绝，且不得继续做未证明的金额投影。 |
| 成本中心 Commitment 服务 | 不适用 | 对象范围为成本中心，不能替代 WBS/内部订单承诺。 |
| Commitment Item 服务 | 不适用 | 属于承诺项目主数据，不是未清承诺行项目。 |

因此 WBS 正式承诺请求返回 `partial/wbs_commitment_source_unavailable`，内部订单返回 `partial/internal_order_commitment_source_unavailable`；两者都必须保持 `commitment_details=[]`、`commitment_totals=null`、`evidence_complete=false`。

## 仍在阻塞的验收项

- `commitment_evidence`
- `wbs_commitment_source_unavailable`
- `internal_order_commitment_source_unavailable`
- `budget_ledger_ambiguous`
- `currency_not_comparable`
- `plan_evidence`
- `wbs_mode_acceptance`
- `internal_order_mode_acceptance`
- `test_data_gap`
- `free_query_comparison`

原始 SAP URL、凭据、响应和未脱敏业务行只保存在 ignored 本地制品；公开报告仅记录状态、计数、关系检查和哈希。
