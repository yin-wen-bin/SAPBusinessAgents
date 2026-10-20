# WorkBuddy 本地启动检查

## 范围

此优化只涉及 WorkBuddy 的本地读取、检查调度和证据解析，不修改 SDK/CLI/worker、模型协议、业务规则、Codex 或用户配置。源码候选存在不等于正式服务已经切换。离线测试不授予业务 PASS，也不自动应用 SDK 认证。

## 单次同步检查

`workbuddy_inspection.py` 提供同一环境、线程和 asyncio 任务内的短生命周期检查上下文。嵌套的 Manager、安装读取、证书投影和待清理检查共享本次已验证观察值；结果深拷贝，异常不缓存。离开上下文即关闭，复制的 ContextVar、其他线程/任务或已经让出事件循环的任务不能继续使用旧观察值。SDK、排队和网络等待期间不持有该上下文。

同一轮只读取/解析一次管理状态、遍历一次安装进行内容核验、扫描一次历史作业并解析一次证书内共用 JUnit。安装根路径不在每个文件中重复解析；文件元数据读取合并，不删除目录范围、硬链接或普通文件检查。退出时仍核对状态、文件身份、目录集合、worker 和证据/源码内容的安全围栏；变化使本轮失败，而不是返回旧的就绪状态。安装首次使用及原完整性缓存失效时，完整内容散列策略不变。

文件身份包括设备/文件编号、类型、链接数、大小和修改/变更时间。worker 在完整性缓存命中时仍核验内容。证书先读取实际使用的字节并核对摘要，不再为同一读取额外打开文件预散列；共用 JSON/JUnit 单轮解析一次。检查退出时关键内容再次核对，以免复用掩盖检查中的修改。下一请求重新检查，不用展示缓存授权。

派发准备在原管理锁的同一线程内使用检查上下文，结束之后仍执行原取消、截止时间、站点和认证修订检查。等待队列前后的两次 reconcile 不合并：其间可能出现新的清理问题。持有维护锁的线程归属、原预算和最多十秒清理不变。

## 历史作业与索引边界

本期只实现**单轮完整扫描一次**：`pending()` 的重复读取复用同轮完整检查，退出时确认文件集合及观察到的文件身份未变；下一轮仍完整扫描。旧的格式损坏记录仍报错，不能因已完成就忽略。

本期**不启用持久活动索引**。现有写入涉及 API、认证、验证及冻结历史程序，使用逐记录锁；没有可证明覆盖所有写入者的全局索引生命周期围栏。仅凭本进程内存或目录修改时间，无法证明不存在遗漏的运行/cleanup_pending 作业。新索引需单独完成所有写入路径接入、进程启动前登记、清理确认后退出、崩溃恢复、旧写入者退出及受控迁移。未满足前继续完整扫描，不建立旁路准入。

## 诊断与认证

本地检查 debug 诊断只含总耗时和状态/安装/证书/作业阶段耗时，不记录命令、凭据、原始推理或 SAP 行。健康和 SDK 状态仍使用原异步展示投影；实际准入只消费完整检查。

新的辅助文件纳入完整 SDK 基础设施及 WorkBuddy 操作依赖。修改后的候选不能沿用旧 SDK 证书；后续维护须用目标回归和适用原生通信证据审核新增认证。模型协议文件未变，不因本地性能优化自动触发探针或重新登录。原模型检查、历史 PASS 和原证书保留，不改写摘要或将测试成功变成业务资格。

## 离线测量

区分进程首次检查、热检查、HTTP 展示和手动认证网络检查。基准对正式旧路径与冻结候选执行相同本地读取，使用正常账户，不启用性能剖析器。记录输入文件/作业数、实际读取次数和时间；不把组件基准冒充端到端 SDK 启动或模型性能。

本地目标为热检查约两秒、展示 GET 一秒内；冷检查保留完整散列，受磁盘/操作系统缓存影响，据实报告。手动认证检查仍保留六十秒预算，不为测性能自动发起认证。

English: Request-local, synchronous inspections reuse verified disk observations only within the same environment/thread/task. State, file-set, worker, evidence and source fences remain mandatory. No cache crosses dispatch, queue waits or requests. Historical jobs are still fully checked once per inspection; persistent active indexing is deferred until all writers can be covered by a proven lifecycle protocol. SDK certification changes require separately audited target evidence; model checks and business PASS are never invented, rewritten or silently transferred.
