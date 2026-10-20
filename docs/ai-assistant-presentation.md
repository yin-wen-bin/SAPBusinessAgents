# AI assistant presentation / AI 助手精简展示

Agent 草稿和工作流沿用原布局，共享宽度调节、历史分组和结果展示规则。

- 左侧手柄支持拖动、方向键（16px）及双击/Home恢复默认。宽度偏好分别保存在本机；窄屏临时限宽不覆盖偏好，550px及以下固定自适应。
- 历史轮次及审计记录归入默认关闭的历史入口，最新轮单独显示。刷新保留展开选择，未完成及待确认事项有独立定位入口。
- 页面标注位于标题旁，具有可见文字。标注引用、授权、未保存内容检查及原执行模式不变。
- 新的修改回复要求模型给出简明业务摘要，复用原summary/answer字段，不增加模型请求或改变Schema。说明与澄清不强制压缩。
- 平台结果优先于模型自述。失败、结果未知、无保存依据的回合不显示“修改已保存”；保存后的提醒不等同于试运行、验收或发布成功。
- 历史修改回复使用保存状态及Diff生成兼容提示，不截取旧技术报告、不重写历史。解释和澄清原文保留。
- 技术详情及原始上下文标签不再显示，原始回复、工具诊断、修订和引用仍保存在后台。Diff按需展开。

## Compatibility and verification

The internal `business-summary/1` marker identifies replies produced with the new
presentation instruction. It is added by the shared decoder, not accepted as an
extra SDK output field. Existing records without the marker remain readable.
No public routes, output schemas, authorization, budgets, SDK settings, or
historical PASS records are migrated. Codex and WorkBuddy use the same final-reply
instruction; investigation, editing and testing are not restricted.

Offline checks cover source-backed save status, failed/unknown outcomes, legacy
replies, 15-round history, layout limits, bilingual headings, keyboard/pointer
resizing and SSE state preservation. Browser tests intercept all requests and
cannot call the production API, a model or SAP.

Prompt changes participate in the existing operation dependency digests.
This does not grant new business PASS records; model protocol and SDK integration
bindings are checked independently. Deployment evidence and the exact impact list
are retained in the delivery batch rather than rewriting prior assessments.
