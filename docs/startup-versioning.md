# Version-aware local startup / 按版本启动

准备期间若源码、依赖绑定或前端内容变化，启动器先释放切换锁，自动重新准备一次；重新冻结、预构建并执行完整准入，不继续使用旧候选。连续变化则保留原服务并给出可操作提示，不显示预期阻断的Python堆栈。`preparation-attempts.json`保留每轮阶段与变更文件/依赖名称，不保存配置或凭据值。切换后的来源变化和其他准入/清理故障不自动重试。

普通双击启动器检查实际运行的源码和前端构建绑定，版本一致时直接打开；不再默认强制重启。源码包含未提交、未跟踪的运行文件，不能仅用 Git HEAD 或修改时间判断。API 从不可变源码归档加载；健康响应的 `startup_binding` 只说明加载来源，不能授权模型、SAP 或管理操作。

`restart-sap-business-agents.cmd` 或普通启动器的 `-Restart` 是显式重启。活动任务、待清理进程、维护锁及回退要求不会因此跳过。仅前台变化只切换前台。旧 API 无可靠版本绑定时，先审核上一版本源码和回退候选；不能核对时保留旧服务并报告阻碍。

等待业务空闲时每五秒检查，不持有切换锁，不阻止正常任务；关闭启动器只退出等待，不取消查询。切换时使用部署锁和短暂维护屏障，拒绝新任务，保留取消和读取。后台调度也检查屏障。孤立维护标记拒绝继续更新，需核对所属进程及日志后恢复，不假定服务安全。

普通启动不清理旧构建。`-RestartSiteOnly` 保留前台独立切换入口；`-RebuildSite` 显式要求重新验证构建。构建先完整校验所有 Agent（包含停用项），再生成活动目录，只读取一次。

启动配置只保存不可逆内容摘要，不复制`.env`或外部SAP凭据文件。活动检查与维护屏障使用实际配置的数据根；站点构建和源码归档仍保留在项目专属目录。不会为了性能修改任何业务资格摘要算法。

冻结启动同时为平台包和归档内的 `scripts` 命名空间核对来源与文件摘要。正式验收辅助模块在候选接入审核和API就绪前预加载；缺失、篡改或预先从可变工作区加载时拒绝切换。不将当前工作目录加入Python导入路径来补救。准备阶段失败保留 `NOT_TESTED`、安全错误代码及组件名，前台明确显示尚未开始SAP取证，未执行阶段不能显示为已完成；旧失败记录不改写、不自动重发。

Formal acceptance uses the system default Runtime and its saved model. The setup GET provides a stable digest; new clients submit `expectedRuntimeDigest` and no manual model override. A changed binding is rejected before campaign creation. WorkBuddy still requires a current compatible concrete model identity; aliases never silently resolve to another model. Legacy `runtimeModelId` requests retain their existing strict behavior. Retries with the same request ID recover the original campaign before consulting a new default.

正式验收设置保持 1–5 组必选案例、完整预检及每次独立取证。摘要在 642px 及以上为一行，窄屏可换行；案例折叠只影响展示。敏感参数、原始 PASS、固定绑定与发布保护不变。页面读取和布局测试不等于一次正式验收。
