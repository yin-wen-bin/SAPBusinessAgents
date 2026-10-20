# AI 助手页面标注 / AI assistant page annotations

## 使用方法

在 Agent 草稿或工作流创建／编辑页打开 AI 助手，点击输入框工具栏中的“页面标注”图标。选择业务控件，在控件旁填写意见并保存；继续选择其他对象，最后点击浮动工具栏的“发送”。无需再填写一遍总要求。也可使用 Ctrl+Enter 保存当前意见。

每批最多20处，每处1–2000字。重复选择同一对象会编辑原意见。列表支持定位、修改和删除。退出标注保留本页尚未发送的意见；离开业务对象前会提示。未发送意见不会持久保存到浏览器，刷新前请先发送或自行保留。

对话历史默认折叠，仅最新一轮展开。手动展开或折叠不会被同一轮进度更新覆盖；折叠不会删除历史上下文。历史 Diff 仍绑定各自回合。

## 边界

- 必须使用已保存草稿和当前修订。未保存内容先显式保存，再重新核对引用；过期标注不得自动迁移。
- Agent 支持草稿字段、README、规则及已绑定的检查／试运行／验收诊断。技术 ID、发布、验收状态等仍按原专用流程控制。
- 工作流支持名称、描述、输入输出定义、节点和连接映射。节点和连接核对稳定对象身份与修订，不用旧数组位置指向新对象。
- 标注属于上下文，不等于修改授权。工作流“仅修改已选字段”仍默认不勾选；显式勾选才限定候选可应用的路径。引用和意见不进入工作流定义。
- 平台按钮、布局、浏览器工具栏及未映射对象不可由此修改。首期不自动截图，不把页面文字、坐标或 CSS 选择器视为授权。
- Agent 草稿的 WorkBuddy 修改请求默认采用可信本地模式；解释仍不得修改。工作流原有模式和确认规则保持不变。工作副本不是操作系统级沙盒；平台 SAP 工具仍逐次只读授权，原生命令的既有边界不变。
- 请求不确定时只能核对或重传原 requestId 与正文；不能修改标注后把它当作同一请求重新发送。确认受理后才清空本批意见。

## 接入其他页面

共享控制器与浮层位于 `site/src/components/AssistantAnnotations.tsx`。页面通过 `AnnotationHost` 提供业务对象范围、修订、目标解析、定位、过期判定，以及现有提交回调。DOM 元素只在本页内定位，不发送给后端。

Agent 适配现有 `annotations[]`，增加 `readme`、`rules` 固定内容路径类型；工作流适配现有 `references[]` 的可选 `comment`、`objectId`、`objectKind`。旧请求缺少新增字段时保持原指纹。服务端仍核对归属、修订、权限、Schema和候选，不新增执行路由或 SDK 分支。

其他页面只需实现业务映射，无需复制选取、高亮、编号和编辑逻辑。只读页面必须仅接入解释；共享组件不会赋予修改、发布或运行权限。

## English

Open the AI assistant and choose **Annotate page** in its input toolbar. Select a supported business control, save a comment, add more targets, then choose **Send**. Annotation-only requests are supported. Ctrl+Enter saves the current comment. Desktop uses anchored editors; narrow screens use a bottom panel.

Up to 20 targets and 2,000 characters per comment are allowed. Exit keeps unsent comments in the current page session, but they are not durable across refreshes. Only the latest conversation round is expanded by default; manual disclosure choices survive progress updates.

References are bound to the saved object and revision. Workflow nodes and connections also bind stable object identities. Stale references require explicit reselection. Comments are context, not permission: original authentication, candidate checks, fixed Agent bindings, scope restrictions and publication confirmations remain in force. Platform UI source is not editable through this feature.

The shared host adapter is Provider-neutral. Agent-draft WorkBuddy revisions use the selected trusted-local product default; explanation stays read-only. Workflow execution-mode confirmation is unchanged. The work copy is not an OS sandbox and SAP platform tools remain authorized per read-only call.

Transport uncertainty freezes the original request. Check or retransmit that exact request; do not dispatch a changed batch as the same request. Existing APIs and historical fingerprints remain compatible.
