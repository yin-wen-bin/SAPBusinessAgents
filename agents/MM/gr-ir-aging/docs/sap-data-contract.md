# GR/IR Evidence Contract

生产编排层先验证 Thin SAPClaw `runtime_health.read_only=true`，再读取 `API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube`。查询必须按公司代码和关键日服务端过滤，并跟随所有分页。

Evidence 根对象包含：

- `metadata.source`, `metadata.system`, `metadata.client`, `metadata.run_id`, `metadata.read_only=true`
- `completeness.complete=true`, `completeness.source_complete=true`, `completeness.has_next=false`
- `entities.A_OperationalAcctgDocItemCube`

每个会计项目至少包含公司代码、年度、会计凭证、项目、PO、PO 项目、过账日期、借贷标识、公司代码币种金额与币种。建议同时读取供应商、物料、工厂、GR/IR 科目、PO 数量/单位、交易币金额/币种、原始参考凭证、发票和冲销字段。

业务键是 `CompanyCode/FiscalYear/AccountingDocument/AccountingDocumentItem`。编排层不得为了通过检查而去重；发现重复应标记不完整。证据文件不得包含凭据、银行数据或无关供应商个人信息。

该 Agent 只消费 evidence 并执行确定性规则。认证、重试、OData URL、分页游标和限流属于 Thin SAPClaw 编排层；SAP GUI Skill 不参与主路径。
