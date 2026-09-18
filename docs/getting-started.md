# 首次使用 / Getting started

[返回 README / Back to README](../README.md)

## 中文

### 安装前

本地执行环境面向 Windows。Python 最低3.11，推荐CI使用的3.13；Node.js最低22.13.0，CI使用Node 22。先安装Git、Python及Node.js，准备只读SAP账户。SDK版本不代表模型一定可用，登录与模型检查在系统配置中单独完成。

按[根README安装步骤](../README.md#首次安装与启动)从 `v0.1.0` 标签克隆并创建本地 `main`，再建立虚拟环境、安装Python依赖，并在site目录执行`npm ci`。仅解压源码ZIP可以浏览或运行，但没有Git仓库时不能使用Agent发布流程。只在`.env`不存在时复制`.env.example`；填写SAP_BASE_URL、SAP_USERNAME、SAP_PASSWORD、SAP_CLIENT等本地OData配置。也可使用受保护的SAPBA_SAP_ENV_FILE。需要Skill的功能请继续完成[README中的SAPSkillhub安装与离线核对](../README.md#安装平台批准的-sapskillhub-skill)；SAPSKILLHUB_ROOT须指向仓库外固定提交的绝对路径，ADT连接和来源Profile另外配置。启用自由查询或Agent对话编写前，先阅读[full access安全边界](../SECURITY.md)。

### 启动与第一次检查

运行仓库根目录的`start-sap-business-agents.cmd`，或README中的PowerShell启动器。默认页面为[中文主页](http://127.0.0.1:4321/zh/)，API为`http://127.0.0.1:8765`。启动器检查依赖、构建或复用站点缓存，然后启动服务；仅启动不等于执行SAP业务查询。

修改端口时使用启动器的`-ApiPort`和`-SitePort`；不要误停另一个工作树的服务。前端应连接同一个实例的API。开发者也可在一个终端运行`.\.venv\Scripts\sap-business-agents.exe --port 8765`，在另一终端进入site后运行`npm run dev`。

固定Agent无须启用Runtime，但仍需可用的SAP连接、所引用的Skill及通过验收的活动版本。`/api/tools/skills`列出5个批准Skill只能证明本机包可用，不能证明目标SAP的ADT权限、固定来源Profile或业务证据有效；`sap-control-object-commitment-evidence`目前未验证，不在批准目录中。需要自由查询、岗位匹配或自然语言编写时，依次完成[Runtime设置](runtime-settings.md)。[在线目录](https://yin-wen-bin.github.io/SAPBusinessAgents/zh/)没有你的本地数据库、登录或SAP连接。

### 常见问题

| 现象 | 建议检查 |
|---|---|
| Python包、Astro或模块不存在 | 确认使用项目虚拟环境；执行Python安装步骤及site中的npm ci |
| 端口已被占用 | 查看占用者，复用正确实例或选择其他端口；不要直接结束不明进程 |
| 页面打开但本地API不可用 | API是否启动、地址端口是否一致；静态目录不能代替本地API |
| Runtime未启用或无默认值 | 登录、目录刷新、模型检查、启用、选择默认Runtime是独立步骤 |
| 模型不兼容、目录过期 | 刷新当前SDK目录并检查候选；不自由输入型号、不静默回退 |
| SDK已更新但仍显示旧版本 | 按系统配置提示重启本地API，重新刷新目录与兼容性检查 |
| Agent尚未验收或受阻 | 查看详情中的缺口及原验收记录，不通过编辑状态强行开启 |
| 所需Skill未列于`/api/tools/skills` | 核对SAPSKILLHUB_ROOT绝对路径、Skillhub固定提交，并在干净Skillhub副本上执行README中的换行准备步骤；再核对Schema和当前Python环境。API健康不等于Skill可用 |
| Skill已列出但SAP证据仍缺失 | 分别核对Skill自有ADT连接、TLS、目标SAP权限和来源Profile；安装成功不等于目标系统真机验收 |
| 查询完成但结论无法确认 | 先看证据缺口；完整空结果与读取失败不同，未知不能视为零 |

## English

### Before installation

The local environment targets Windows. Python 3.11 is the minimum; CI uses/recommends 3.13. Node.js requires at least 22.13.0; CI uses Node 22. Install Git, Python and Node.js and obtain a read-only SAP account. SDK installation does not prove model compatibility; login and model checks are separate settings steps.

Follow the [README installation](../README.md#first-installation-and-startup): clone the `v0.1.0` tag, create a local `main`, create a virtual environment, install Python dependencies, and run `npm ci` in site. A source ZIP can be browsed or run, but Agent publication requires the Git repository. Copy `.env.example` only when `.env` does not exist. Set local OData connection values or use a protected SAPBA_SAP_ENV_FILE. For Skill-dependent features, complete the [SAPSkillhub setup and offline checks in the README](../README.md#install-platform-approved-sapskillhub-skills): SAPSKILLHUB_ROOT must be an absolute path to the pinned checkout outside the repository, with separate ADT and source-profile configuration. Read the [full-access security boundary](../SECURITY.md) before enabling free queries or conversational Agent authoring.

### Start and check

Run `start-sap-business-agents.cmd` from the root or the README PowerShell launcher. Open the [English home](http://127.0.0.1:4321/en/); the API defaults to `http://127.0.0.1:8765`. The launcher checks dependencies, builds/reuses the site and starts services. Startup alone is not a SAP business query.

Use `-ApiPort` and `-SitePort` for different ports. Do not terminate another worktree's service; connect the frontend to the same instance's API. Developers may run `.\.venv\Scripts\sap-business-agents.exe --port 8765` in one terminal and `npm run dev` inside site in another.

Fixed Agents need no Runtime, but still require SAP access, referenced Skills and an active accepted version. The five approved IDs shown by `/api/tools/skills` prove only that local packages are available, not that the target SAP grants ADT access, has valid fixed-source profiles or can provide business evidence. `sap-control-object-commitment-evidence` remains unvalidated and is not approved. Configure [Runtime settings](runtime-settings.md) for free queries, role matching and natural-language authoring. The [online catalog](https://yin-wen-bin.github.io/SAPBusinessAgents/en/) has no local database, login or SAP connection.

### Troubleshooting

| Symptom | Check |
|---|---|
| Missing Python package or Astro module | Use the project virtual environment; install Python dependencies and run npm ci inside site |
| Port in use | Identify its owner; reuse the correct instance or choose other ports, never kill an unknown process |
| Page loads but API is unavailable | API process, address and port; a static catalog is not a local API |
| Runtime disabled or no default | Login, catalog refresh, compatibility check, enablement and default selection are separate |
| Incompatible model or stale catalog | Refresh the SDK catalog and check candidates; no arbitrary IDs or silent fallback |
| Old SDK version after update | Restart the API as instructed, refresh the catalog and repeat model checks |
| Agent blocked/not yet tested | Read its gaps and acceptance record; do not bypass gates by editing status |
| Required Skill absent from `/api/tools/skills` | Check the absolute SAPSKILLHUB_ROOT path and pinned checkout; follow the README line-ending preparation steps on a clean Skillhub checkout, then check schemas and Python dependencies. A healthy API does not prove Skill readiness |
| Skill listed but SAP evidence missing | Check the Skill-owned ADT connection, TLS, target SAP permissions and source profiles separately; installation is not live acceptance |
| Finished query but inconclusive result | Read evidence gaps; complete empty results differ from failures, and unknown is not zero |
