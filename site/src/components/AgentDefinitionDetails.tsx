import type { AgentDefinition, AgentPresentation, Locale, LocalizedText } from "../lib/types";

type Props = {
  agent: AgentDefinition | Record<string, any>;
  presentation?: AgentPresentation | Record<string, any> | null;
  locale: Locale;
  idPrefix?: string;
};

function localized(value: LocalizedText | Record<string, string> | undefined, locale: Locale) {
  return value?.[locale] || value?.[locale === "zh" ? "en" : "zh"] || "";
}

function normalizedToolName(value: unknown) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function declaredToolFor(step: any, executionStep: any) {
  const candidates = [executionStep.tool_id, executionStep.operation]
    .map(normalizedToolName)
    .filter(Boolean);
  return (Array.isArray(step.tools) ? step.tools : []).find((tool: any) => {
    const name = normalizedToolName(tool?.name);
    const kind = normalizedToolName(tool?.kind);
    const purpose = normalizedToolName(`${tool?.purpose?.zh || ""} ${tool?.purpose?.en || ""}`);
    if (candidates.some((candidate) => name === candidate || purpose.includes(candidate))) return true;
    if (executionStep.executor === "sap_read") return kind.includes("provider") && (kind.includes("sap") || name.includes("sap"));
    if (executionStep.executor === "skill") return kind.includes("skill");
    if (executionStep.executor === "rule") return kind.includes("rule");
    return false;
  });
}

export default function AgentDefinitionDetails({ agent, presentation, locale, idPrefix = "" }: Props) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const projection: any = presentation || (agent as any).presentation || {};
  const odata = Array.isArray(projection.odata_objects) ? projection.odata_objects : [];
  const tables = Array.isArray(projection.tables) ? projection.tables : [];
  const unknownSkills = Array.isArray(projection.unknown_skill_objects) ? projection.unknown_skill_objects : [];
  const projectedWorkflow = new Map((Array.isArray(projection.workflow) ? projection.workflow : []).map((item: any) => [item.id, item]));
  const workflow = ((agent as any).workflow || []).map((step: any) => ({
    ...step,
    execution_step_ids: step.executionStepIds || [],
    execution_steps: (projectedWorkflow.get(step.id) as any)?.execution_steps || [],
  }));
  const issues = (Array.isArray(projection.issues) ? projection.issues : []).filter((item: any) => item.blocking);
  const modules = Array.isArray(projection.sap_modules) ? projection.sap_modules : ((agent as any).sapModules || []);
  const transactions = Array.isArray(projection.transactions) ? projection.transactions : ((agent as any).transactions || []);
  const sectionId = (name: string) => idPrefix ? `${idPrefix}-${name}` : name;

  return <>
    <section id={sectionId("sap-scope")} className="shared-agent-definition agent-panel">
      <h2>{tr("SAP范围", "SAP scope")}</h2>
      <p>{tr("以下范围从已保存的资料和确定性执行定义生成；它描述定义中的调用，不代表每次运行都执行了全部条件步骤。", "This scope is generated from saved metadata and deterministic execution. It describes configured calls, not whether every conditional step ran in a particular run.")}</p>
      <div className="scope-grid">
        <div><h3>{tr("SAP业务组件", "SAP business components")}</h3><ul className="tag-list scope-tags">{modules.map((item: string) => <li key={item}>{item}</li>)}</ul></div>
        {transactions.length > 0 && <div><h3>{tr("事务码", "Transactions")}</h3><ul className="tag-list scope-tags">{transactions.map((item: string) => <li key={item}><code>{item}</code></li>)}</ul></div>}
        <div className="shared-scope-wide"><h3>{tr("核心OData实体", "Core OData entities")}</h3>
          {odata.length ? <ul className="tag-list scope-tags odata-api-tags">{odata.map((item: any) => <li key={`${item.service}|${item.version}|${item.entity}`}><code>{item.service} / {item.entity}</code><span className="odata-version-badge">{`V${String(item.version).split(".")[0]}`}</span>{item.conditional && <small>{tr("满足条件时调用", "Called when its condition is met")}</small>}</li>)}</ul> : <p>{tr("定义中没有可确认的OData实体。", "No OData entity can be confirmed from the definition.")}</p>}
        </div>
        <div className="shared-scope-wide"><h3>{tr("直接表与Skill内部对象", "Direct tables and Skill objects")}</h3>
          {tables.length > 0 && <ul className="tag-list scope-tags">{tables.map((item: any) => <li key={`${item.name}|${item.skill_id || ""}`}><code>{item.name}</code>{item.skill_id && <span> · {item.skill_id}</span>}{item.conditional && <small>{tr("满足条件时调用", "Called when its condition is met")}</small>}</li>)}</ul>}
          {projection.scope_mode === "odata_only" && <p>{tr("通过OData访问，未直接读取SAP表。", "Accessed through OData; no SAP table is read directly.")}</p>}
          {unknownSkills.map((item: any) => <p key={`${item.execution_step_id}|${item.skill_id}`}>{tr("内部对象未声明", "Internal objects not declared")}: <code>{item.skill_id}</code>{item.conditional && <small> · {tr("满足条件时调用", "called when its condition is met")}</small>}</p>)}
          {!tables.length && !unknownSkills.length && projection.scope_mode !== "odata_only" && <p>{tr("没有声明可确认的直接表或Skill内部对象。", "No confirmed direct table or Skill-internal object is declared.")}</p>}
        </div>
      </div>
      {issues.length > 0 && <aside className="agent-presentation-diagnostics" role="status"><h3>{tr("资料待完善", "Documentation needs attention")}</h3><ul>{issues.map((item: any, index: number) => <li key={`${item.code}|${item.path}|${index}`}>{localized(item.message, locale)} {item.path && <code>{item.path}</code>}</li>)}</ul></aside>}
    </section>

    <section id={sectionId("workflow-tools")} className="shared-agent-definition agent-panel">
      <h2>{tr("工作流与 Tools", "Workflow & Tools")}</h2>
      <p>{tr("从业务输入到安全输出的完整执行路径。每一步均列出对应 SAP 范围与实际使用的 Tool。", "The complete execution path from business input to safe output. Every step identifies its SAP scope and the Tools it uses.")}</p>
      <ol className="agent-workflow">
        {workflow.map((step: any, index: number) => <li className="workflow-step" key={step.id || index}>
          <div className="workflow-step-heading"><span className="workflow-index">{String(index + 1).padStart(2, "0")}</span><div><small>{tr("步骤", "Step")} {index + 1}</small><h3>{localized(step.title, locale)}</h3></div><code>{step.id}</code></div>
          <p>{localized(step.description, locale)}</p>
          {Array.isArray(step.operations?.[locale]) && step.operations[locale].length > 0 && <div className="step-operations"><h4>{tr("详细操作", "Detailed operations")}</h4><ol>{step.operations[locale].map((operation: string, operationIndex: number) => <li key={operationIndex}>{operation}</li>)}</ol></div>}
          <div className="step-tools"><h4>{tr("本步骤 API / SAPSkill / Tools", "APIs, SAPSkills & tools used at this step")}</h4>
            {step.execution_steps?.length ? <ul>{step.execution_steps.map((executionStep: any) => { const entities = odata.filter((item: any) => item.execution_step_ids.includes(executionStep.id)); const declaredTool = declaredToolFor(step, executionStep); return <li key={executionStep.id}><div className="tool-heading"><code>{declaredTool?.name || executionStep.tool_id}</code><span>{declaredTool?.kind || executionStep.executor}</span></div>{declaredTool?.purpose && <p>{localized(declaredTool.purpose, locale)}</p>}<p>{tr("实际配置", "Configured operation")}: <code>{executionStep.tool_id}</code> · {tr("执行步骤", "execution step")}: <code>{executionStep.id}</code>{executionStep.conditional && <small> · {tr("满足条件时调用", "Called when its condition is met")}</small>}</p>{entities.length > 0 && <p>{entities.map((item: any) => item.entity).join(" · ")}</p>}</li>; })}</ul> : <p>{tr("没有可显示的执行映射。", "No execution mapping is available for display.")}</p>}
          </div>
        </li>)}
      </ol>
    </section>
  </>;
}
