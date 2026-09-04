# AR催收与银行来款核对边界

`ar-collection 1.0.0`与`ar-cash-application 0.1.0`采用两个独立的严格只读职责：

- `ar-collection`按客户批量重建应收账龄、催收冻结和催收状态，并生成异常优先的工作清单。它不读取银行来款。
- `ar-cash-application`核对银行来款、客户子分类账、清账凭证和发票关系。它不执行清账、过账或自动销账。

FI清账只证明SAP应收项目的清账关系，不能替代独立银行到账证据。银行付款人名称和原始参考号只进入Windows用户绑定的加密受限制品；公开结果只保留业务键、金额、币种、状态、掩码账户和带域的HMAC。

当前活动的`ar-collection 0.1.0`在新版本三级真机验收通过前保持不变。新版本和新Agent均保持`NOT_TESTED/executable=false`，不能通过普通业务入口运行。验收必须比较独立只读基线、Skill、自由查询和固定Agent，并确认所有OData调用均为GET。

## Skill执行与受限数据

固定Agent、组合工作流、自由查询和验收共用同一份已批准Skill目录。Skill必须同时满足只读、已验证、可用、Schema有效、输出策略已声明以及包和Profile指纹匹配。

受限制品Reveal使用本机Origin、CSRF和一次性短时Token。明细默认保留30天，可由用户提前永久删除；删除后仅保留不含业务值的审计墓碑。
