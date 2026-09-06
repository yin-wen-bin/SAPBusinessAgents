# order-to-cash-status 0.1.1 真机回归报告

- 测试日期：`2026-09-06T15:29:30+08:00`
- 代码基线：`bc63100+working-tree`
- 系统与客户端：已脱敏；连接配置和凭据不落库
- Embedded Provider：`embedded-odata` `2.0.0`，严格GET-only
- 输入契约：仅接受一个必填数字型`sales_order`
- 安全边界：未执行任何SAP写操作。
- 技术状态：`completed`
- 业务结论：`部分通过`

## 定义一致性

中英文摘要和README均限定为“从销售订单出发，追踪交货、PGI、开票和FI清账状态”。页面没有宣称支持客户PO、交货单、发票号或客户日期范围入口。执行图、状态规则和输出契约在0.1.1中未修改。

## 真机结果

固定Agent使用一个真实销售订单号完成回归，共执行9次Embedded GET。`source_complete=true`、`business_complete=true`，输出1条销售订单项目粒度记录。

| 阶段 | 结果 |
| --- | --- |
| 销售订单 | confirmed |
| 销售订单项目 | confirmed |
| 交货 | confirmed |
| PGI | confirmed |
| 开票 | confirmed |
| FI清账 | not_confirmed |

由于真实样本未确认FI清账，Agent业务状态保持`partial`，没有把“已开票”误报为“已清账”。银行到账仍属于未被证明的扩展证据边界，不改变本轮公开入口只支持销售订单号的定义。

## 验收结论

页面定义、单一输入Schema和固定Agent调用一致，0.1.1回归通过。原三级验收的执行图与业务比较Hash仍有效；本轮只更新了公开文案并验证销售订单单入口。原始SAP行、业务标识、URL和凭据仅保存在被忽略的本地证据目录。
