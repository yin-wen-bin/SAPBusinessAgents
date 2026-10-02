# Agent 草稿助手交付与验证 / Draft assistant delivery verification

日期 / Date: 2026-09-29. 本次只修改草稿助手，不启用 WorkBuddy、不调用真实模型或 SAP、不发布 Agent、不提交或推送代码。 / This delivery changes only the draft assistant. It does not enable WorkBuddy, call real models/SAP, publish Agents, commit, or push.

## 环境 / Environment

- 项目 `.venv`：Python 3.13.5，`openai-codex==0.147.0` 及绑定 CLI，`pip install -e ".[dev]"`。安装 SDK 不等于完成账号登录或真实模型兼容性验收。 / The project virtual environment installs the pinned SDK and CLI; installation does not certify authentication or model compatibility.
- 前端按锁文件 `npm ci` 安装 React、`@astrojs/react`、Astro 及检查工具。Node 最低版本调整为 22.19.0，以满足锁文件中的依赖要求。 / Frontend dependencies use the lockfile; the Node minimum is 22.19.0 to match locked transitive requirements.
- 本机项目专用 Node 22.23.3 位于忽略目录 `.local/node-runtime`，来自 Node.js 官方站点并校验 SHA-256。使用 `. .\.local\use-node.ps1` 仅改变当前 PowerShell 的 PATH，不修改系统 Node。 / The locally installed runtime is verified against official checksums; the helper changes only the current shell PATH.
- Edge 测试工具位于忽略目录 `.local/browser-tools`。内嵌浏览器连接不可用，验收使用外部 Edge 与合成 API 拦截。 / External Edge with synthetic API interception is used because the in-app browser bridge is unavailable.

## 已交付 / Delivered

安全 Markdown、折叠技术详情、语义对象导航、多控件编号标注、标注定位／编辑／删除、单回合可审核 Diff、草稿定义范围控制、修订与证据摘要核对、递归敏感数据过滤、显式澄清模式和选项、长会话投影、SSE 游标恢复及轮询回退、队列与撤回、截图期限清理、选中文本引用、独立 WorkBuddy 反馈与模拟 Provider 验证。解释与修改的进度提示分开显示；窄屏标注时助手收起为操作条，避免遮挡待标注控件。 / Delivered: safe rendering, semantic object navigation, numbered multi-control annotations, reviewable Diff, draft-only edits, revision-bound references, recursive redaction, explicit clarification options, conversation projections, resumable platform events, polling fallback, queue/withdrawal, attachment cleanup, bounded text references, and independent mock-tested WorkBuddy feedback. Explanation and revision progress have distinct labels; compact annotation controls keep the target fields accessible on narrow screens.

原始历史记录和已发布 Agent 验收不改写。Codex `full_access` 不是操作系统安全沙盒；提示词限制与控制器拒绝平台变更不能阻止所有本机命令旁路。 / Historical records and published Agent acceptance remain unchanged. SDK full access is not an OS security sandbox; prompts and controller rejection do not prevent every native-command bypass.

## 验证命令 / Verification commands

```powershell
. .\.local\use-node.ps1
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_agent_feedback_context.py tests/test_workbuddy_feedback.py tests/test_agent_feedback_session.py tests/test_agent_feedback_experience.py tests/test_agent_feedback_safety.py tests/test_agent_feedback_runtime_binding.py tests/test_agent_authoring.py tests/test_agent_draft_operations.py tests/test_runtime_router.py tests/test_authoring_tools.py tests/test_authoring_workspace.py tests/test_authoring_harness.py tests/test_runtime_changesets.py tests/test_harness.py tests/test_assistant_tools.py
Set-Location site
npm run check
npm run build
node --test --test-reporter=dot tests/*.test.mjs
npm run preview -- --host 127.0.0.1 --port 4322
$env:SAPBA_PLAYWRIGHT_MODULE='D:\SAPBusinessAgents\.local\browser-tools\node_modules\playwright'
node tests/browser/agent-assistant-actions.mjs
npm run preview -- stop
```

Browser tests use only synthetic draft definitions and intercepted API requests.
They block external network requests, WebSockets and Service Workers. Screenshots
are generated under ignored `.local-data/ui/agent-assistant-actions/` directories.

## 最终验证结果 / Final results

| 检查 / Check | 结果 / Result |
| --- | --- |
| 草稿助手及相关后端回归 / Related backend regression | 244 passed; 1 dependency deprecation warning |
| 前端单测 / Frontend unit tests | 103 passed |
| Astro check | 49 files; 0 errors, 0 warnings, 0 hints |
| Manifest validation | 34 manifests across 6 modules |
| Production build | 82 static pages |
| Edge browser acceptance | zh/en × 1440px/550px/390px: all 6 passed |
| Working-tree whitespace check | `git diff --check` passed |

Python 的唯一警告来自 Starlette TestClient 对 httpx 的旧接口，不影响本次测试。 / The only Python warning concerns a deprecated Starlette TestClient/httpx interface; the tests passed.

浏览器覆盖键盘提交、原请求幂等重传、解释／修改意图、多标注一次生成修订、标注定位、对话内 Diff、过期引用拒绝、澄清选项、文本引用、SSE 中断后的轮询回退、不重复提交、无水平溢出及无页面 JavaScript 错误。浏览器截图已经人工复看。 / Browser checks cover keyboard submission, idempotent retransmission, distinct intents, batched annotations, navigation, inline Diff, stale-reference rejection, clarification choices, text references, polling fallback, no repeated submission, no horizontal overflow, and no page JavaScript errors. Screenshots were also visually reviewed.

本机最终截图目录 / Final local screenshots: `D:\SAPBusinessAgents\.local-data\ui\agent-assistant-actions\2026-09-29T06-35-40-999Z`.

测试未重跑已发布固定 Agent 的 SAP 验收，未重启真实 API 服务；原有 `outputs/` 未读取或更改。 / Published fixed-Agent SAP acceptance was not rerun, the real API was not restarted, and existing `outputs/` was neither read nor changed.

## 未启用能力 / Deliberately unavailable capabilities

- 跨回合 Provider 原生 thread 续接和运行中 steer 未启用；平台上下文与队列可用。 / Native cross-turn resume and live steering are disabled; platform context and queueing are available.
- WorkBuddy 不支持截图输入，服务端明确拒绝。真实账号和模型验收需要单独授权，不因为存在适配器就视为可用。 / WorkBuddy rejects images. Live account/model certification requires separate authorization.
- 自由拖框截图标注仍未实现；当前交付为稳定语义引用的控件标注。 / Free-form screenshot box annotations are not implemented; control-level semantic annotations are delivered.
