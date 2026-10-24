# Sample discovery readiness / 测试数据查找条件

必填与可自动查找是两个维度。公司代码／工厂等必要组织范围须由用户明确输入；截止日期等不等式边界不能从候选记录反推用户意图。

查样窗口分别展示手工条件、已填写且继续保留的条件、可自动查找参数。手工条件不显示禁用复选框，可返回并定位到对应输入框。仅在必要手工条件齐备后才允许启动查样。

静态定义中存在 lt/le/gt/ge 输入绑定时显示 `range_boundary`，不再误报映射缺失。实际读取仍保留全部已提供过滤、原范围授权、实时Schema与证据校验；此展示和静态预检变更不读取SAP、不补值、不修改草稿定义。

Required inputs are not necessarily discoverable. Organizational scope and query boundaries must be entered explicitly. Existing values remain query conditions. The discovery UI separates manual conditions, retained values and selectable sample fields; returning to an input does not start a job. Static range bindings are diagnosed accurately without widening reads or inferring cutoff values.

## 本次任务的 Runtime / Per-task Runtime

新前台自动采用系统默认 Runtime，不显示选择器或“更改本次 Runtime”入口。预检只展示当前信息，不授予执行资格，也不调用认证、模型或SAP探针。实际使用信息以服务端首次受理的冻结快照为准，包含账号修订、站点、模型、worker、接入认证及所需操作／权限。

请求以 `runtimePolicy: "system_default"` 声明意图。服务端先以业务参数、所选字段和此策略执行幂等预约，再解析并完整校验当时的默认 Runtime，受理后持久化实际快照。请求指纹不包含会变化的预览摘要；同编号换正文冲突，同内容重传先返回原任务，不重新读取默认值。新编号才形成新任务。排队和派发时只复核保存的绑定，不用新默认替换；已撤销认证、安装变化或权限不足仍阻断。

草稿历史绑定、旧任务、系统默认及配置不修改。旧客户端的显式选择格式继续兼容；不携带策略或选择时仍走原严格路径，不通过缺省字段隐式迁移。自动策略与显式选择不能同时提供。Codex保留既有查样模型与推理策略，不触发自动兼容探针。

启动失败保存安全的真实错误码、启动阶段、请求编号和启动耗时；未执行状态不能显示为正在读取SAP。响应未知仍只核对原任务，不宣称已停止、不自动重发。此功能上线不等于实际查样通过。

New tasks automatically use the system default Runtime. The page shows usage only; a preview never authorizes execution. The server fingerprints the default-policy intent and business scope, captures and verifies the actual default once, then persists its frozen snapshot. A retransmission returns the original task before reading changed defaults. Historical bindings/settings and legacy request fingerprints remain untouched. Actual dispatch rechecks the saved binding and retains every authorization, scope, Schema, evidence and result check.
