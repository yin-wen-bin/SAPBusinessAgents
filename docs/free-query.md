# 自由查询 / Free query

## 中文

从顶部“自由查询”进入独立查询页。描述业务对象、组织范围、日期及想确认的问题。缺口工作流带入的上下文供复核，不是已取得的SAP证据。银行参考号只填默认折叠的安全参数，不写在问题正文。

开始后查看实际阶段、耗时与当前活动；需要澄清时先回答页面的问题。终态先展示业务结论和证据缺口，进度与技术详情可展开。查询完成不等于业务完成；“下载结果”提供现有制品，受限数据需单独授权查看。

点击“开始只读查询”后立即显示提交状态和等待耗时。平台仍须完成 Runtime 的安装、认证、模型及资格检查，不能用系统配置页的展示缓存代替准入。取得运行编号后自动进入进度页。连接中断或提交结果未知时，不自动重发，请先通过历史记录核对，避免重复查询。

进度页区分 SDK 连接、问题提交、等待模型、工具读取及结果核验。SDK 消息不等于 SAP 读取进展，等待期间也会显示耗时。事件流以外仅定期读取已有运行状态，不重新执行查询；隐藏页面暂停轮询。手动停止、后退或跳转离页会取消所属查询；刷新、切标签、关闭页面或失联不自动取消，任务仍受原有超时限制。重开原页面或历史记录只恢复查看进度、结果及取消状态，不重复执行。

结果旁的“继续对话”打开输入弹窗；关闭或按Esc不取消任务，未发送输入保留。对话历史整体默认折叠，展开查看不改变继续对话的参考来源或任务归属。反馈分析进度可在刷新后恢复。仅重新解释时复用已验证证据，0次SAP GET；明确重新查询会创建新轮次，仍执行原安全检查及逐次授权。确认结果符合预期后，可创建待审核固定Agent草稿；草稿不是已发布或可执行Agent。

执行状态以平台记录为准，历史轮次的取消不是当前轮次取消。进度条是阶段指示，不是完成百分比；未执行的SAP读取不能标成已完成。没有可靠运行事件时显示“等待进展更新”。

### 多轮提交兼容契约

新前台每次明确发送携带 `requestId` 和 `continuationMode: "review"`，普通与恢复续聊共同判断重新查询、证据复用、澄清或新会话。恢复来源仍绑定轮次、归属和内容摘要；它不是新增SAP读取授权。旧恢复请求未携带该字段时保持仅使用历史证据的行为；显式 `evidence_only` 不得派发重新查询。

同编号同正文返回原反馈请求，不同正文冲突。受理收据与轮次、证据链接及会话更新原子保存，排队交接失败保留原轮次，由现有调度恢复核对，不创建第二轮。响应未知时只GET核对原请求，不自动重发。当前与历史执行事实分别传递；模型文字不能覆盖平台取消或清理状态。完整历史原文保留，显示兼容提示不修改原记录或PASS。

维护时可在项目根目录执行 `powershell -ExecutionPolicy Bypass -File scripts/Start-SAPBusinessAgents.ps1 -RestartSiteOnly -NoBrowser -PreserveSiteBuilds`，预构建并验活后只切换前台，保留 API、运行中查询和旧构建。它要求已有健康服务和可核对的回退构建，不执行模型或资格检查。慢准入移出事件循环、仅检查所选 Provider 的后端候选，仍须独立完成资格证据承接与受控上线；不能因为前台已更新就推定该后端候选已生效。

## English

Use the Free query navigation entry. Describe business objects, organization, dates and the question to resolve. Workflow-gap context is for review, not acquired SAP evidence. Enter bank references only in the collapsed secure-parameters section, never in question text.

Watch actual stages, elapsed time and current activity; answer clarification requests first. Terminal views prioritize conclusions and gaps; progress and technical detail expand on demand. A completed query is not completed business work. Downloads expose existing artifacts, with separate authorization for restricted data.

Submitting a query immediately shows its pending state and elapsed wait. Full installation, authentication, model and qualification checks still apply; settings-page display caches cannot authorize execution. The progress page opens after a run ID is returned. An uncertain submission is never automatically retried: check Query history first to avoid a duplicate query.

The progress page distinguishes SDK connection, submission, model waiting, tool reads and result validation. SDK messages do not prove SAP read progress. Elapsed time remains visible while waiting. Fallback refresh only reads the existing run; it never executes the query again. Hidden pages pause polling. Manual Stop, Back or navigation away cancels the page's own query. Reloading, switching tabs, closing a page or disconnecting does not automatically cancel; the existing task timeout still applies. Reopening the original page or Query history only restores progress, results or cancellation status without executing again.

Open inline feedback after the result to request corrections; persisted feedback progress survives refresh. Reinterpretation reuses verified evidence with zero SAP GETs; expanded querying requires safety checks. Accepting a result can create a review-only fixed-Agent draft, not a published/executable Agent.

From the repository root, the launcher option `-RestartSiteOnly -NoBrowser -PreserveSiteBuilds` prebuilds, checks and switches only the frontend, preserving the API, running queries and old builds. Healthy existing services and a verified rollback build are required. It does not run model or qualification checks. The backend candidate that moves admission checks off the event loop and inspects only the selected Provider needs separate evidence adoption and controlled deployment; frontend deployment does not activate it.
