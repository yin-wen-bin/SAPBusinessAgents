import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = [
  path.resolve(scriptDirectory, "..", ".."),
  process.cwd(),
  path.resolve(process.cwd(), ".."),
].find((candidate) => existsSync(path.join(candidate, "config", "agent-presentation-contract.json")));
if (!repositoryRoot) throw new Error("Agent presentation contract root was not found");
const contract = JSON.parse(readFileSync(path.join(repositoryRoot, "config", "agent-presentation-contract.json"), "utf8"));

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function packageDigest(manifest) {
  return `sha256:${createHash("sha256").update(JSON.stringify(canonical(manifest))).digest("hex")}`;
}

function localIssue(code, pathValue, zh, en, { invalid = false, blocking = true } = {}) {
  return { code, path: pathValue, severity: invalid ? "error" : "warning", blocking, message: { zh, en } };
}

function placeholder(value, values) {
  return values.some((item) => String(item).trim().toLowerCase() === String(value ?? "").trim().toLowerCase());
}

function normalizedToolName(value) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function declaredToolMatches(tool, executionStep) {
  const name = normalizedToolName(tool?.name);
  const kind = normalizedToolName(tool?.kind);
  const purpose = normalizedToolName(`${tool?.purpose?.zh || ""} ${tool?.purpose?.en || ""}`);
  const candidates = [executionStep.tool_id, executionStep.operation]
    .map(normalizedToolName)
    .filter(Boolean);
  if (candidates.some((candidate) => name === candidate || purpose.includes(candidate))) return true;
  if (executionStep.executor === "sap_read") return kind.includes("provider") && (kind.includes("sap") || name.includes("sap"));
  if (executionStep.executor === "skill") return kind.includes("skill");
  if (executionStep.executor === "rule") return kind.includes("rule");
  return false;
}

function valueAtPath(value, pathValue) {
  return pathValue.split(".").reduce((current, key) => (
    current && typeof current === "object" ? current[key] : undefined
  ), value);
}

function literalNames(value) {
  const values = Array.isArray(value) ? value : [value];
  return values
    .filter((item) => typeof item === "string")
    .map((item) => item.trim())
    .filter((item) => item && !item.includes("{{") && /^[A-Za-z0-9_./-]+$/.test(item));
}

function collectOData(value, onReference) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectOData(item, onReference));
    return;
  }
  if (!value || typeof value !== "object") return;
  if (
    typeof value.service_name === "string"
    && typeof value.entity_set === "string"
    && typeof value.odata_version === "string"
  ) {
    onReference({
      service: value.service_name,
      version: value.odata_version,
      entity: value.entity_set,
    });
  }
  Object.values(value).forEach((item) => collectOData(item, onReference));
}

function addAssociation(target, workflowId, executionId, conditional) {
  if (workflowId && !target.workflow_step_ids.includes(workflowId)) target.workflow_step_ids.push(workflowId);
  if (executionId && !target.execution_step_ids.includes(executionId)) target.execution_step_ids.push(executionId);
  target.conditional ||= conditional;
}

export function deriveAgentPresentation(manifest) {
  const issues = [];
  const isAssistant = manifest?.kind === "platform_assistant";
  const workflow = Array.isArray(manifest?.workflow) ? manifest.workflow : [];
  const executionSteps = Array.isArray(manifest?.execution?.steps) ? manifest.execution.steps : [];
  const executionById = new Map(executionSteps.map((step) => [step.id, step]));
  const workflowByExecution = new Map();

  for (const [workflowIndex, step] of workflow.entries()) {
    const mapped = Array.isArray(step.executionStepIds) ? step.executionStepIds : [];
    for (const executionId of mapped) {
      if (workflowByExecution.has(executionId)) {
        issues.push(localIssue(
          "presentation_execution_step_mapped_multiple_times",
          `workflow[${workflowIndex}].executionStepIds`,
          `执行步骤 ${executionId} 被多个业务步骤引用。`,
          `Execution step ${executionId} is mapped by more than one business step.`,
          { invalid: true },
        ));
      } else {
        workflowByExecution.set(executionId, { id: step.id, index: workflowIndex });
      }
      if (!executionById.has(executionId)) {
        issues.push(localIssue(
          "presentation_execution_step_missing",
          `workflow[${workflowIndex}].executionStepIds`,
          `业务步骤引用了不存在的执行步骤 ${executionId}。`,
          `The business step references missing execution step ${executionId}.`,
          { invalid: true },
        ));
      }
    }
  }
  for (const [executionIndex, step] of executionSteps.entries()) {
    if (!workflowByExecution.has(step.id)) {
      issues.push(localIssue(
        "presentation_execution_step_unmapped",
        `execution.steps[${executionIndex}].id`,
        `执行步骤 ${step.id} 未映射到业务步骤。`,
        `Execution step ${step.id} is not mapped to a business step.`,
        { invalid: true },
      ));
    }
  }

  const odataMap = new Map();
  const tableMap = new Map();
  const toolMap = new Map();
  const unknownSkills = [];
  const projectedWorkflow = workflow.map((step, workflowIndex) => ({
    id: step.id,
    execution_step_ids: Array.isArray(step.executionStepIds) ? step.executionStepIds : [],
    execution_steps: [],
    index: workflowIndex,
  }));

  for (const [executionIndex, step] of executionSteps.entries()) {
    const mapping = workflowByExecution.get(step.id);
    const workflowId = mapping?.id;
    const conditional = Boolean(step.when);
    const toolId = step.executor === "skill"
      ? (step.skillId || step.operation)
      : step.executor === "sap_read"
        ? `sap_read.${step.operation}`
        : step.operation;
    const toolKey = `${step.executor}|${toolId}|${conditional}`;
    if (!toolMap.has(toolKey)) {
      toolMap.set(toolKey, {
        id: toolId,
        executor: step.executor,
        operation: step.operation,
        skill_id: step.skillId || null,
        conditional,
        workflow_step_ids: [],
        execution_step_ids: [],
      });
    }
    addAssociation(toolMap.get(toolKey), workflowId, step.id, conditional);

    collectOData(step.request, (reference) => {
      const key = `${reference.service}|${reference.version}|${reference.entity}`;
      if (!odataMap.has(key)) {
        odataMap.set(key, {
          ...reference,
          conditional,
          workflow_step_ids: [],
          execution_step_ids: [],
        });
      }
      addAssociation(odataMap.get(key), workflowId, step.id, conditional);
    });

    const tablePaths = step.executor === "skill" ? contract.table_skills[step.skillId] : undefined;
    if (Array.isArray(tablePaths)) {
      for (const pathValue of tablePaths) {
        for (const name of literalNames(valueAtPath(step, pathValue))) {
          if (!tableMap.has(name)) {
            tableMap.set(name, {
              name,
              source: "skill_contract",
              skill_id: step.skillId,
              conditional,
              workflow_step_ids: [],
              execution_step_ids: [],
            });
          }
          addAssociation(tableMap.get(name), workflowId, step.id, conditional);
        }
      }
    } else if (step.executor === "skill") {
      unknownSkills.push({
        skill_id: step.skillId || step.operation,
        workflow_step_id: workflowId || null,
        execution_step_id: step.id,
        conditional,
      });
    }

    if (mapping) {
      projectedWorkflow[mapping.index].execution_steps.push({
        id: step.id,
        executor: step.executor,
        operation: step.operation,
        tool_id: toolId,
        conditional,
        when: step.when || null,
      });
    }
  }

  if (!isAssistant) {
    if (typeof manifest?.owner !== "string" || !manifest.owner.trim() || placeholder(manifest.owner, contract.business_domain_placeholders)) {
      issues.push(localIssue(
        "presentation_business_domain_missing",
        "owner",
        "请填写明确的业务域，不能使用 Unassigned 等占位值。",
        "Provide a specific business domain instead of a placeholder such as Unassigned.",
      ));
    }
    const hasSapRead = executionSteps.some((step) => step.executor === "sap_read");
    const modules = Array.isArray(manifest?.sapModules) ? manifest.sapModules : [];
    if (hasSapRead && (!modules.length || modules.every((item) => placeholder(item, contract.sap_module_placeholders)))) {
      issues.push(localIssue(
        "presentation_sap_modules_missing",
        "sapModules",
        "SAP读取Agent需要填写具体SAP业务组件，不能只使用 Common。",
        "An SAP-reading Agent needs specific SAP business components rather than only Common.",
      ));
    }
    for (const [index, item] of (Array.isArray(manifest?.tables) ? manifest.tables : []).entries()) {
      if (placeholder(item, contract.object_placeholders)) {
        issues.push(localIssue(
          "presentation_object_placeholder",
          `tables[${index}]`,
          `对象资料 ${item} 是占位值；请填写可追溯对象，或在纯OData场景使用空数组。`,
          `${item} is a placeholder. Declare a traceable object, or use an empty array for an OData-only Agent.`,
        ));
      }
    }
    for (const [index, step] of workflow.entries()) {
      for (const field of ["title", "description"]) {
        if (!step?.[field]?.zh?.trim() || !step?.[field]?.en?.trim()) {
          issues.push(localIssue(
            `presentation_workflow_${field}_missing`,
            `workflow[${index}].${field}`,
            `业务步骤 ${index + 1} 缺少双语${field === "title" ? "标题" : "说明"}。`,
            `Business step ${index + 1} is missing a bilingual ${field}.`,
          ));
        }
      }
      if (
        !Array.isArray(step?.operations?.zh) || !step.operations.zh.length
        || !Array.isArray(step?.operations?.en) || !step.operations.en.length
        || step.operations.zh.length !== step.operations.en.length
      ) {
        issues.push(localIssue(
          "presentation_workflow_operations_missing",
          `workflow[${index}].operations`,
          `业务步骤 ${index + 1} 需要数量一致的中英文详细操作。`,
          `Business step ${index + 1} needs matching Chinese and English detailed operations.`,
        ));
      }
      const configured = projectedWorkflow[index]?.execution_steps || [];
      const declared = Array.isArray(step?.tools) ? step.tools : [];
      for (const executionStep of configured) {
        if (!declared.some((tool) => declaredToolMatches(tool, executionStep))) {
          issues.push(localIssue(
            "presentation_tool_declaration_mismatch",
            `workflow[${index}].tools`,
            `业务步骤 ${index + 1} 声明的工具与执行步骤 ${executionStep.id} 的实际配置不一致。`,
            `The tools declared by business step ${index + 1} do not match execution step ${executionStep.id}.`,
            { invalid: true },
          ));
        }
      }
    }

    const declaredTables = new Set((Array.isArray(manifest?.tables) ? manifest.tables : []).filter((item) => !placeholder(item, contract.object_placeholders)));
    for (const name of tableMap.keys()) {
      if (!declaredTables.has(name)) {
        issues.push(localIssue(
          "presentation_declared_table_missing",
          "tables",
          `Skill契约可确认直接读取 ${name}，但Agent未声明该表或对象。`,
          `The Skill contract confirms a direct read of ${name}, but the Agent does not declare it.`,
          { invalid: true },
        ));
      }
    }
    if (!unknownSkills.length) {
      for (const name of declaredTables) {
        if (!tableMap.has(name)) {
          issues.push(localIssue(
            "presentation_declared_table_untraceable",
            "tables",
            `已声明的表或对象 ${name} 无法追溯到读取步骤或已知Skill契约。`,
            `Declared table or object ${name} cannot be traced to a read step or known Skill contract.`,
            { invalid: true },
          ));
        }
      }
    }
  }

  const blocking = issues.filter((item) => item.blocking);
  const status = blocking.some((item) => item.severity === "error")
    ? "invalid"
    : blocking.length
      ? "needs_input"
      : "ready";
  const odataObjects = [...odataMap.values()];
  const tables = [...tableMap.values()];
  const scopeMode = unknownSkills.length
    ? "partially_declared"
    : tables.length
      ? "direct_tables_declared"
      : odataObjects.length
        ? "odata_only"
        : "no_sap_objects";
  return {
    contract_version: contract.version,
    package_digest: packageDigest(manifest),
    status,
    issues,
    business_domain: manifest?.owner || "",
    sap_modules: Array.isArray(manifest?.sapModules) ? manifest.sapModules : [],
    transactions: Array.isArray(manifest?.transactions) ? manifest.transactions : [],
    declared_tables: Array.isArray(manifest?.tables) ? manifest.tables : [],
    odata_objects: odataObjects,
    tables,
    unknown_skill_objects: unknownSkills,
    tools: [...toolMap.values()],
    workflow: projectedWorkflow,
    scope_mode: scopeMode,
  };
}

export function presentationContract() {
  return structuredClone(contract);
}
