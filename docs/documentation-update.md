# 文档更新交付核对 / Documentation delivery review

日期 / Date: 2026-09-08。审查基线 / Baseline: `7140e8749868be01db28fdcec6e8e352783d7918`。

## 已更新 / Updated

- 当前32个Agent、6个模块、3个工作流均有使用说明；数量是本次基线，不是生成器常量。README保留中英文锚点、同范围双语说明、业务入口、首次安装和结果解读。
- 新增首次使用、Runtime设置、自由查询和开发指南。补齐Node及npm ci；说明在线静态目录与本地执行的区别。模型目录不固定数量或型号。
- AR催收与银行来款已按当前生产状态说明；生产订单成本差异不再误标阻塞或描述为通用物料价格分析。P2P说明批量入口，MRP不再声称调用未声明的Skill。
- 原CLI/fixture说明移入离线回归资料；原历史报告不改结论。三个workflow.json均未修改。
- 状态按原验收模式双语显示；纯文案补丁显示“复用原版本验收”，原SAP日期、Provider、比较、范围、限制和报告路径全部保留。
- scripts/documentation.py提供--write和--check，复用正式目录发现，检查当前README覆盖、受控索引、本地文件链接和锚点；不扫描历史版本或改写叙述。

All current Agent/module/workflow guides were refreshed. New installation, Runtime, free-query and developer guides explain task selection, startup and result boundaries. Historical CLI material is separated from web execution. Workflow definitions and historical reports remain unchanged. Generated facts and local links/anchors are checked without a documentation database.

## 已发布 / Locally published

以下16个patch通过生命周期服务发布，在**隔离工作树**中保持活动投影。没有替换原工作区main，也没有重启其服务或推送远端。旧包先归档（本地提交9568a30），文档及门禁修复单独提交，再逐Agent发布。

The following patches were published through the lifecycle service in the isolated worktree. The original main checkout/services and remote remain unchanged.

| Agent | 版本 | 本地发布提交 |
|---|---|---|
| `ap-payment` | 0.2.1 | `03ff092` |
| `gr-ir-clearing` | 0.2.1 | `86626a6` |
| `intelligent-sourcing-rfq` | 0.1.1 | `bc0892c` |
| `material-shortage-procurement-response` | 0.2.1 | `3e4062f` |
| `procure-to-pay-status` | 0.3.2 | `4d98e97` |
| `supplier-performance-risk` | 0.2.3 | `2c1ebcc` |
| `demand-forecast-planning` | 0.3.1 | `042bd30` |
| `mrp-exception-analysis` | 0.2.1 | `dc24581` |
| `production-order-monitoring` | 0.2.1 | `b008c94` |
| `production-variance-analysis` | 0.2.1 | `cafa14a` |
| `billing-block-diagnosis` | 0.2.1 | `f635e20` |
| `billing-completeness-check` | 0.1.1 | `1fe638f` |
| `delivered-not-billed` | 0.2.1 | `1eb0dfc` |
| `delivery-delay-prediction` | 0.1.1 | `c22ff1b` |
| `due-delivery-prioritization` | 0.1.1 | `92d1233` |
| `order-to-cash-status` | 0.1.2 | `4bf8b33` |

每个版本报告记录source_version、source_validation_digest、execution_digest与sap_get_count=0。清单documentationReuse单独记录文案复核时间；该时间不是新的SAP验收时间。所有执行定义、输入输出Schema、受控规则声明和规则源码摘要与基线一致，原验收业务字段未改变。

Each release keeps original acceptance facts and records reuse provenance separately; zero SAP GETs were made. Execution, I/O schemas and managed-rule declarations/source digests match the baseline.

## 保留草稿 / Review-only drafts

以下9个受阻Agent只更新正式使用文档；清单文案改进保存在隔离实例的needs_review草稿中，没有发布或增加执行资格。岗位匹配助理按平台助理维护，不进入固定Agent发布流程。

| Agent | 目标版本 | 草稿 |
|---|---|---|
| `budget-rolling-forecast` | 0.1.2 | `agent_draft_ad73e0635eef476f` |
| `co-month-end-allocation-settlement` | 0.1.2 | `agent_draft_db0d3b15b2a24e74` |
| `cost-center-expense-anomaly` | 0.1.2 | `agent_draft_3fb856301c044a95` |
| `internal-order-project-control` | 0.4.1 | `agent_draft_8649dafd249c4fb7` |
| `production-scheduling-capacity` | 0.1.1 | `agent_draft_e5eb394e84c14560` |
| `billing-dispute-classification` | 0.1.1 | `agent_draft_7460ab1cdf5a4726` |
| `billing-output-monitor` | 0.1.1 | `agent_draft_61bfa6d5f8c94520` |
| `returns-credit-anomaly` | 0.1.1 | `agent_draft_9e226eae844040d9` |
| `shortage-allocation-advisor` | 0.1.1 | `agent_draft_4d8f984668dc4c8e` |

草稿存在此工作树的本地数据库和.prototype目录，不随Git合并或推送迁移。可在[隔离Agent管理](http://127.0.0.1:44321/zh/agent-management/)复核；原工作区管理中心没有被写入这些草稿。

Drafts live in the isolated database/.prototype and are not transferred through Git. Review them in the isolated management UI; the original instance was not modified.

## 验证 / Verification

- 文档生成与本地链接/锚点检查覆盖53份当前说明与指南。
- Python：生命周期26项通过、1项既有跳过；文档与验收复用4项通过；Manifest/工作流编排、编写、展示45项通过；运行平台的Manifest/历史版本/工作流相关12项通过。合计87项通过、1项跳过。
- 前端全套41项通过；Astro检查0错误、0警告；生产构建78页。
- 中文/英文 × 1440/772/390px × 31个活动目录页面，186项浏览器检查通过；实际查看代表性截图。未提交任何查询，隔离数据库业务运行数为0。
- 从全新虚拟环境安装Python依赖并执行npm ci，通过启动器启动API 48765和Preview 44321。API健康正常，实际加载16个补丁版本和目录摘要均可核对。此次安装使用Python 3.14.5、Node 24.16.0；CI声明仍是Python 3.13、Node 22。
- 三个工作流的原固定版本和摘要均能解析；各Agent活动/停用状态与基线一致。
- 未修改既有versions、历史验收JSON或正式workflow.json；没有新增凭据、原始业务数据或本地制品到提交范围。旧包原有空行按原样保留，不对历史文件重新格式化。

Tests: 87 relevant Python cases passed (one existing skip), 41 frontend tests, clean Astro diagnostics and a 78-page build. All 186 localized/responsive browser checks passed. A fresh dependency environment started on isolated ports without submitting a business run. Pinned workflows resolve and lifecycle/behavior fingerprints are unchanged.

## 现存限制与边界 / Remaining limitations

- 原月结功能修改仍留在原工作区。隔离提交基线的月结工作流固定引用通过检查；原工作区未提交清单变化引起的引用不匹配应在对应功能交付中处理，本轮未重新绑定。
- 付款准备度工作流仍为inconclusive，保留原用户接受的银行扣款、付款运行和银行主数据证据缺口；两个停用工作流保持停用。
- 未重新执行SAP真机验收、查询SAP业务数据、登录模型或验证实际SAP连接；文案发布不产生新的业务验收声明。所有Runtime和Skill门禁保持原有规则。
- 干净依赖环境的FastAPI/Starlette出现两类弃用警告，本次无相关回归失败；未修改第三方依赖范围。宽范围全平台测试曾提前停止，交付统计仅含上述已完成的相关回归，不声称全库Python测试完成。
- 本地预览只对应隔离工作树。最终分支为`codex/documentation-refresh`；未合并main、未推送。合并时需要单独复核原工作区月结功能与详情页的交叉修改。

The original month-end work remains separate. Accepted workflow evidence gaps and inactive states remain. No SAP/model live acceptance or real SAP connection test was performed. Two dependency deprecation warnings remain; the completed Python suite is the scoped regression listed above, not the interrupted broad platform run. No merge or push occurred.
