# Agent创建规范 / Agent authoring standard

本规范是固定Agent候选的共同资料契约。Agent管理、自由查询生成、工作流缺口生成和仓库内Codex编写都应遵循本页，并由[`config/agent-presentation-contract.json`](../config/agent-presentation-contract.json)的版本化规则执行机器检查。目录展示、草稿预览和发布前检查使用同一个无副作用提取器。 / This is the shared documentation contract for fixed-Agent candidates. Agent management, free-query generation, workflow-gap generation and repository-based Codex authoring all follow it. Versioned machine checks live in [`config/agent-presentation-contract.json`](../config/agent-presentation-contract.json); catalog rendering, draft preview and pre-publication checks use the same side-effect-free extractor.

## 字段边界 / Field boundaries

| 字段 / Field | 含义 / Meaning |
|---|---|
| `catalog_module` | 当前目录分组和导航；它不是SAP能力声明。 / Current catalog grouping and navigation; it is not an SAP capability declaration. |
| `manifest.module` | 发布包所在的原始仓库模块。已发布包不因目录调整而移动。 / Repository module of the published package. Catalog changes do not move an immutable package. |
| `owner` | 业务域，例如`MM-PUR / MM-IM`。键名为兼容性保留，页面统一显示“业务域 / Business domain”。 / Business domain, for example `MM-PUR / MM-IM`. The key remains for compatibility; the UI labels it Business domain. |
| `sapModules` | 实际SAP业务组件，可跨模块，例如`MM-PUR`和`MM-IM`；不得用目录分类代替。 / Actual SAP business components, including meaningful cross-module scope; never substitute the catalog group. |
| `tables` | 已声明且可从读取步骤或Skill契约追溯的直接表或对象。纯OData访问使用空数组。 / Direct tables or objects traceable to a read step or Skill contract. Use an empty array for OData-only access. |
| `workflow.operations` | 中英文详细业务处理，覆盖范围、筛选或关联、规则、空结果或缺口以及输出。 / Bilingual operational detail covering scope, filters or joins, rules, empty or incomplete evidence, and output. |
| `executionStepIds` | 业务步骤与确定性执行步骤的完整一对一映射。 / Complete, one-to-one mapping from business steps to deterministic execution steps. |

SAP读取候选不得以`Unassigned`、仅`Common`或`SAP OData entity`等占位内容冒充完整资料。机器检查只要求可以核对的结构和引用；业务说明是否清楚、是否符合实际仍由草稿共享预览和现有Diff审核确认。未知Skill内部对象必须显示“内部对象未声明”，不得猜测底层表。 / SAP-reading candidates must not use placeholders such as `Unassigned`, only `Common`, or `SAP OData entity` as complete documentation. Machine checks verify structures and traceable references; clarity and business accuracy remain subject to the shared draft preview and existing Diff review. Unknown Skill internals are shown as undeclared rather than guessed.

## OData Agent完整示例 / Complete OData Agent example

下面片段只展示资料结构；执行定义仍须使用完整Schema、GET-only请求、验收契约和安全规则。 / This excerpt demonstrates presentation metadata only; the execution still requires complete schemas, GET-only requests, an acceptance contract and guardrails.

```json
{
  "owner": "MM-PUR / MM-IM",
  "sapModules": ["MM-PUR", "MM-IM"],
  "tables": [],
  "workflow": [
    {
      "id": "read-po-evidence",
      "title": {"zh": "读取采购订单证据", "en": "Read purchase-order evidence"},
      "description": {
        "zh": "按确认的交货日期读取采购订单计划行并关联订单项目。",
        "en": "Read purchase-order schedule lines for the confirmed delivery date and join their items."
      },
      "operations": {
        "zh": [
          "使用已确认日期筛选计划行；没有结果时返回明确的空结果。",
          "用采购订单和项目组合键关联订单项目；缺少关联证据时保留缺口。"
        ],
        "en": [
          "Filter schedule lines by the confirmed date and return an explicit empty result when no rows match.",
          "Join items by purchase order and item composite key, preserving a gap when evidence is missing."
        ]
      },
      "executionStepIds": ["collect_po_evidence"],
      "tools": [{
        "name": "sap_read_execute_plan",
        "kind": "SAP Read Provider",
        "purpose": {"zh": "执行确定性OData GET计划。", "en": "Execute a deterministic OData GET plan."}
      }]
    }
  ]
}
```

共享提取器从`execution.steps[].request`递归提取服务、OData版本和实体，不要求作者把OData实体重复写入`tables`。 / The shared extractor recursively obtains services, OData versions and entities from `execution.steps[].request`; authors do not duplicate OData entities in `tables`.

## 条件Skill示例 / Conditional Skill example

```json
{
  "id": "read-approved-table",
  "executor": "skill",
  "operation": "execute",
  "skillId": "sap-adt-table-export",
  "readOnly": true,
  "when": {"source": "{{steps.choose_source.output.use_table}}", "equals": true},
  "inputMapping": {"object": "TSTC"}
}
```

页面应显示`TSTC`来自已知Skill契约，并标注“满足条件时调用”。如果Skill没有受支持的对象声明契约，页面显示“内部对象未声明”，候选不会仅因该内部资料未知而被阻断。 / The page shows that `TSTC` comes from a known Skill contract and marks the call as conditional. If a Skill has no supported object-declaration contract, the page states that its internal objects are undeclared; that unknown internal detail alone does not block the candidate.

## 生命周期门禁 / Lifecycle gates

- 已发布Agent继续兼容加载、构建和执行；资料缺口仅作为诊断，不追溯改变Digest或验收。 / Published Agents continue to load, build and run; documentation gaps are diagnostic and do not retroactively alter digests or acceptance.
- 草稿可以在资料未完整时保存、对话和试运行。 / Drafts may be saved, discussed and trialed while documentation remains incomplete.
- 正式验收、验收复用和发布前必须通过当前展示契约。 / Formal acceptance, acceptance reuse and publication require the current presentation contract.
- 平台助理使用独立规则，不要求固定Agent执行步骤映射。 / Platform assistants use separate rules and need no fixed-Agent execution mapping.

自动检查不是SAP试运行或三级验收。资料修复若保持执行、托管规则和验收契约不变，可按生命周期规则复用来源PASS验收，但不得改写原SAP验收日期或比较结论。 / Automatic checks are not an SAP trial or three-stage acceptance. Documentation repairs that preserve execution, managed rules and the acceptance contract may reuse source PASS acceptance under lifecycle rules, without rewriting the original SAP acceptance date or comparison conclusions.
