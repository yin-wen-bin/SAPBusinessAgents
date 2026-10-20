# Agent 管理中心目录投影

目录展示与操作准入分离。列表使用 `GET /api/agents/catalog?view=summary&state=all`，返回 `items` 与 `snapshot`；旧调用不带 `view` 时仍返回完整数组。`GET /api/agents/{id}?view=management` 实时核对单个 Agent 的管理信息，不带参数仍返回原 Manifest。

`snapshot` 的 `instance` 与递增 `id` 标识当前 API 进程的快照，`available` 区分未知与真实空目录；另有 `checked_at`、`stale`、`refreshing`、`error_code` 及安全分段计时。`refresh=true` 只请求同一个后台读取任务，不启动模型、SDK、SAP或业务操作。

进程内快照默认60秒新鲜。目录写入后的审计与仓库代次使展示失效；活跃页面每60秒后台核对相关内容。完整内容摘要及校验器来源绑定可复用已通过的 Manifest 校验，不依赖时间戳授予任何权限。首次或变化时仍调用原校验器；错误保留旧快照但标为过期。后台读取最多一个，锁等待和文件检查不发生在摘要 GET 中。

完整管理读取使用请求内上下文：当前 Manifest、生命周期、版本只装载一次，草稿与运行数量批量读取，工作流引用只扫描一次。管理结果沿用原计算逻辑。创建版本、启停、回滚、删除、发布及执行仍走原始实时安全门禁，不消费展示缓存。

前端保留成功列表，刷新失败不清空；恢复可见时只核对过期数据，正在验证的草稿继续按原进度读取。后台刷新使用1／2／5秒退避 GET，页面隐藏暂停。详情核对前管理控件禁用。源配置、选中对象、表单及焦点不因列表刷新重建。

## English

The management catalog is a display-only, in-process snapshot. The new summary view has freshness and availability metadata; legacy catalog and Manifest responses remain compatible. Management details are read on demand, with request-local batching and the original validators. Every business mutation and execution still performs the existing live authorization and consistency checks.

The 60-second display TTL is not an authorization lifetime. Failed refreshes retain a visibly stale successful snapshot. Background work is single-flight, content changes trigger validation, and late invalidated builds cannot overwrite a newer result. No SDK/model configuration, qualification records, or historical PASS is rewritten.
