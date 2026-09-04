import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const MODULES = ["Common", "FI", "CO", "SD", "MM", "PP"];
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "..", "..");
const agentsRoot = path.join(repositoryRoot, "agents");
const outputPath = path.join(scriptDirectory, "..", "src", "generated", "agents.ts");

function requireString(value, location) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${location} must be a non-empty string`);
}

function requireLocalized(value, location) {
  if (!value || typeof value !== "object") throw new Error(`${location} must be localized`);
  requireString(value.zh, `${location}.zh`);
  requireString(value.en, `${location}.en`);
}

function requireList(value, location) {
  if (!Array.isArray(value) || value.length === 0) throw new Error(`${location} must be a non-empty array`);
}

function requireArray(value, location) {
  if (!Array.isArray(value)) throw new Error(`${location} must be an array`);
}

function validateOutputDisplay(properties, location) {
  const allowedFormats = new Set(["text", "enum", "enum_list", "status"]);
  for (const [name, property] of Object.entries(properties ?? {})) {
    if (!property || typeof property !== "object" || Array.isArray(property)) continue;
    const itemSchema = property.items && typeof property.items === "object" ? property.items : undefined;
    const enumValues = Array.isArray(property.enum)
      ? property.enum
      : (Array.isArray(itemSchema?.enum) ? itemSchema.enum : undefined);
    const display = property["x-sapba-display"];
    const displayLocation = `${location}.${name}.x-sapba-display`;
    if (display === undefined) {
      if (enumValues) throw new Error(`${displayLocation} must localize every public enum`);
      continue;
    }
    if (!display || typeof display !== "object" || Array.isArray(display)) {
      throw new Error(`${displayLocation} must be an object`);
    }
    if (display.visible !== undefined && typeof display.visible !== "boolean") {
      throw new Error(`${displayLocation}.visible must be boolean`);
    }
    const format = display.format ?? "text";
    if (!allowedFormats.has(format)) throw new Error(`${displayLocation}.format is invalid`);
    const labels = display.labels;
    if (enumValues && (!labels || typeof labels !== "object" || Array.isArray(labels))) {
      throw new Error(`${displayLocation}.labels must provide bilingual text for every public enum value`);
    }
    if (labels && typeof labels === "object" && !Array.isArray(labels)) {
      for (const [code, label] of Object.entries(labels)) {
        requireString(code, `${displayLocation}.labels code`);
        requireLocalized(label, `${displayLocation}.labels.${code}`);
      }
      const missing = (enumValues ?? []).filter((value) => !Object.hasOwn(labels, String(value)));
      if (missing.length) throw new Error(`${displayLocation}.labels is missing: ${missing.join(", ")}`);
    }
  }
}

export function validateAgent(agent, expectedModule, expectedSlug, source) {
  if (![1, 2].includes(agent.schemaVersion)) throw new Error(`${source}: unsupported schemaVersion`);
  if (!SLUG_PATTERN.test(expectedSlug)) throw new Error(`${source}: directory must use a lowercase kebab-case slug`);
  if (agent.slug !== expectedSlug) throw new Error(`${source}: slug must match directory '${expectedSlug}'`);
  if (agent.module !== expectedModule) throw new Error(`${source}: module must match directory '${expectedModule}'`);
  requireLocalized(agent.title, `${source}.title`);
  requireLocalized(agent.summary, `${source}.summary`);
  for (const field of ["status", "version", "owner"]) requireString(agent[field], `${source}.${field}`);
  for (const field of ["tags", "sapModules", "systems"]) requireList(agent[field], `${source}.${field}`);
  // OData-only Agents do not execute SAP GUI transactions or read raw tables.
  // Empty arrays are truthful scope declarations, not missing catalog data.
  for (const field of ["transactions", "tables"]) requireArray(agent[field], `${source}.${field}`);
  for (const field of ["inputs", "outputs", "guardrails"]) {
    requireList(agent[field]?.zh, `${source}.${field}.zh`);
    requireList(agent[field]?.en, `${source}.${field}.en`);
  }
  if (agent.kind === "platform_assistant") {
    if (expectedModule !== "Common") throw new Error(`${source}: platform assistants must be in Common`);
    const assistant = agent.assistant;
    if (!assistant || typeof assistant !== "object" || Array.isArray(assistant)) {
      throw new Error(`${source}.assistant must be an object`);
    }
    const expected = {
      type: "role_matching",
      runtimeCapability: "role_matching",
      composable: false,
      localFileAccess: "read_only_user_selected",
    };
    for (const [field, value] of Object.entries(expected)) {
      if (assistant[field] !== value) throw new Error(`${source}.assistant.${field} is invalid`);
    }
    if (agent.execution !== undefined) throw new Error(`${source}: platform assistants cannot declare execution`);
    requireList(agent.workflow, `${source}.workflow`);
    for (const [index, step] of agent.workflow.entries()) {
      requireString(step.id, `${source}.workflow[${index}].id`);
      requireLocalized(step.title, `${source}.workflow[${index}].title`);
      requireLocalized(step.description, `${source}.workflow[${index}].description`);
      requireList(step.tools, `${source}.workflow[${index}].tools`);
      requireArray(step.executionStepIds, `${source}.workflow[${index}].executionStepIds`);
    }
    return;
  }
  requireList(agent.workflow, `${source}.workflow`);
  const stepIds = new Set();
  const scopedSapValues = {
    modules: new Set(),
    transactions: new Set(),
    tables: new Set(),
  };
  const sapScopeFields = {
    modules: "sapModules",
    transactions: "transactions",
    tables: "tables",
  };
  let hasStepSapScope = false;
  for (const [index, step] of agent.workflow.entries()) {
    requireString(step.id, `${source}.workflow[${index}].id`);
    if (stepIds.has(step.id)) throw new Error(`${source}: duplicate workflow step '${step.id}'`);
    stepIds.add(step.id);
    requireLocalized(step.title, `${source}.workflow[${index}].title`);
    requireLocalized(step.description, `${source}.workflow[${index}].description`);
    requireList(step.executionStepIds, `${source}.workflow[${index}].executionStepIds`);
    if (step.operations !== undefined) {
      requireList(step.operations?.zh, `${source}.workflow[${index}].operations.zh`);
      requireList(step.operations?.en, `${source}.workflow[${index}].operations.en`);
      if (step.operations.zh.length !== step.operations.en.length) {
        throw new Error(`${source}.workflow[${index}].operations must contain the same number of zh and en items`);
      }
      for (const locale of ["zh", "en"]) {
        for (const [operationIndex, operation] of step.operations[locale].entries()) {
          requireString(operation, `${source}.workflow[${index}].operations.${locale}[${operationIndex}]`);
        }
      }
    }
    if (step.sapScope !== undefined) {
      if (!step.sapScope || typeof step.sapScope !== "object" || Array.isArray(step.sapScope)) {
        throw new Error(`${source}.workflow[${index}].sapScope must be an object`);
      }
      hasStepSapScope = true;
      let assignedValueCount = 0;
      for (const [scopeField, agentField] of Object.entries(sapScopeFields)) {
        const values = step.sapScope[scopeField];
        if (!Array.isArray(values)) throw new Error(`${source}.workflow[${index}].sapScope.${scopeField} must be an array`);
        for (const [valueIndex, value] of values.entries()) {
          requireString(value, `${source}.workflow[${index}].sapScope.${scopeField}[${valueIndex}]`);
          if (!agent[agentField].includes(value)) {
            throw new Error(`${source}.workflow[${index}].sapScope.${scopeField} contains '${value}' outside agent.${agentField}`);
          }
          scopedSapValues[scopeField].add(value);
          assignedValueCount += 1;
        }
      }
      if (assignedValueCount === 0) throw new Error(`${source}.workflow[${index}].sapScope must assign at least one SAP scope value`);
    }
    requireList(step.tools, `${source}.workflow[${index}].tools`);
    for (const [toolIndex, tool] of step.tools.entries()) {
      requireString(tool.name, `${source}.workflow[${index}].tools[${toolIndex}].name`);
      requireString(tool.kind, `${source}.workflow[${index}].tools[${toolIndex}].kind`);
      requireLocalized(tool.purpose, `${source}.workflow[${index}].tools[${toolIndex}].purpose`);
    }
  }
  if (hasStepSapScope) {
    for (const [scopeField, agentField] of Object.entries(sapScopeFields)) {
      for (const value of agent[agentField]) {
        if (!scopedSapValues[scopeField].has(value)) {
          throw new Error(`${source}: agent.${agentField} value '${value}' is not assigned to any workflow step`);
        }
      }
    }
  }
  const executionStepIds = new Set((agent.execution?.steps ?? []).map((step) => step.id));
  const mappedExecutionStepIds = agent.workflow.flatMap((step) => step.executionStepIds ?? []);
  if (mappedExecutionStepIds.length !== new Set(mappedExecutionStepIds).size) {
    throw new Error(`${source}: workflow maps an execution step more than once`);
  }
  if (mappedExecutionStepIds.length !== executionStepIds.size || mappedExecutionStepIds.some((id) => !executionStepIds.has(id))) {
    throw new Error(`${source}: workflow must map every execution step exactly once`);
  }
  if (agent.schemaVersion === 2) validateExecution(agent.execution, source);
  if (agent.systems.includes("SAP ECC")) throw new Error(`${source}.systems must not advertise SAP ECC`);
  const localizedSchemaTitles = (schema, location, excludeInternalInputs = false) => {
    const result = { zh: [], en: [] };
    for (const [name, property] of Object.entries(schema?.properties ?? {})) {
      if (
        excludeInternalInputs &&
        (property["x-sapba-workflow-only"] === true || property["x-sapba-internal"] === true)
      ) continue;
      requireLocalized(property.title, `${location}.properties.${name}.title`);
      result.zh.push(property.title.zh);
      result.en.push(property.title.en);
    }
    return result;
  };
  if (JSON.stringify(agent.inputs) !== JSON.stringify(localizedSchemaTitles(agent.execution.inputSchema, `${source}.execution.inputSchema`, true))) {
    throw new Error(`${source}.inputs must mirror execution.inputSchema titles`);
  }
  if (!agent.execution.outputSchema) throw new Error(`${source}.execution.outputSchema is required`);
  validateOutputDisplay(
    agent.execution.outputSchema.properties,
    `${source}.execution.outputSchema.properties`,
  );
  if (JSON.stringify(agent.outputs) !== JSON.stringify(localizedSchemaTitles(agent.execution.outputSchema, `${source}.execution.outputSchema`))) {
    throw new Error(`${source}.outputs must mirror execution.outputSchema titles`);
  }
  if (agent.validation !== undefined) validateLiveValidation(agent.validation, source);
}

function validateLiveValidation(validation, source) {
  if (!validation || typeof validation !== "object" || Array.isArray(validation)) {
    throw new Error(`${source}.validation must be an object`);
  }
  if (!["PASS", "PARTIAL", "FAIL", "BLOCKED", "NOT_TESTED"].includes(validation.verdict)) {
    throw new Error(`${source}.validation.verdict is invalid`);
  }
  if (!["complete", "partial", "bounded"].includes(validation.evidenceScope)) {
    throw new Error(`${source}.validation.evidenceScope is invalid`);
  }
  requireString(validation.testedAt, `${source}.validation.testedAt`);
  if (Number.isNaN(Date.parse(validation.testedAt))) {
    throw new Error(`${source}.validation.testedAt must be ISO-8601`);
  }
  requireList(validation.providers, `${source}.validation.providers`);
  requireLocalized(validation.summary, `${source}.validation.summary`);
  requireString(validation.reportPath, `${source}.validation.reportPath`);
  if (!/^docs\/[a-z0-9][a-z0-9._/-]*\.md$/.test(validation.reportPath)) {
    throw new Error(`${source}.validation.reportPath must be a relative docs Markdown path`);
  }
  if (validation.executable !== undefined && typeof validation.executable !== "boolean") {
    throw new Error(`${source}.validation.executable must be boolean`);
  }
  if (validation.acceptanceMode !== undefined && !["three_stage", "deterministic_runtime"].includes(validation.acceptanceMode)) {
    throw new Error(`${source}.validation.acceptanceMode is invalid`);
  }
  for (const field of ["freeQueryComparison", "fixedAgentComparison"]) {
    if (validation[field] !== undefined && !["MATCH", "MISMATCH", "BLOCKED", "NOT_TESTED"].includes(validation[field])) {
      throw new Error(`${source}.validation.${field} is invalid`);
    }
  }
  for (const field of ["codexDirectBaselineHash", "freeQueryHash", "adjudicatedResultHash", "fixedAgentHash", "comparisonHash"]) {
    if (validation[field] !== undefined && !/^sha256:[0-9a-f]{64}$/.test(validation[field])) {
      throw new Error(`${source}.validation.${field} must be a full SHA-256`);
    }
  }
}

function validateExecution(execution, source) {
  if (!execution || typeof execution !== "object" || Array.isArray(execution)) {
    throw new Error(`${source}.execution must be an object for schemaVersion 2`);
  }
  if (execution.mode !== "deterministic") throw new Error(`${source}.execution.mode must be deterministic`);
  if (!execution.inputSchema || execution.inputSchema.type !== "object") {
    throw new Error(`${source}.execution.inputSchema must be an object JSON Schema`);
  }
  if (!execution.inputSchema.properties || typeof execution.inputSchema.properties !== "object") {
    throw new Error(`${source}.execution.inputSchema.properties must be an object`);
  }
  rejectMalformedTemplates(execution.steps, `${source}.execution.steps`);
  rejectMalformedTemplates(execution.outputMapping, `${source}.execution.outputMapping`);
  for (const [name, property] of Object.entries(execution.inputSchema.properties)) {
    if (!property || typeof property !== "object" || Array.isArray(property)) continue;
    const marker = property["x-sapba-server-default"];
    if (marker !== undefined && typeof marker !== "boolean" && marker !== "business_date") {
      throw new Error(`${source}.execution.inputSchema.properties.${name}.x-sapba-server-default must be boolean or business_date`);
    }
    if (marker === true && !Object.hasOwn(property, "default")) {
      throw new Error(`${source}.execution.inputSchema.properties.${name}.x-sapba-server-default=true requires a default value`);
    }
    if (marker === true) {
      validateSchemaDefault(
        property.default,
        property,
        `${source}.execution.inputSchema.properties.${name}`,
      );
    }
    if (marker === "business_date" && property.type !== "string") {
      throw new Error(`${source}.execution.inputSchema.properties.${name}.x-sapba-server-default=business_date requires a string date field`);
    }
  }
  requireList(execution.steps, `${source}.execution.steps`);
  if (!execution.acceptance || execution.acceptance.comparisonMode !== "business_semantic") {
    throw new Error(`${source}.execution.acceptance.comparisonMode is invalid`);
  }
  requireList(execution.acceptance.businessKeys, `${source}.execution.acceptance.businessKeys`);
  for (const field of ["facts", "metrics", "requiredLimitations"]) {
    if (!Array.isArray(execution.acceptance[field])) throw new Error(`${source}.execution.acceptance.${field} must be an array`);
  }
  if (execution.acceptance.schemaVersion === "2.0") {
    for (const field of ["decimalFields", "decimalMetricIds", "currencyFields", "unitFields", "dateFields"]) {
      if (!Array.isArray(execution.acceptance[field])) throw new Error(`${source}.execution.acceptance.${field} must be an array`);
    }
    for (const field of ["inputDefaults", "constantDefaults", "fieldAliases", "fieldExtractors", "currencyFromDecimal", "valueMappings", "limitationKeywords"]) {
      if (!execution.acceptance[field] || typeof execution.acceptance[field] !== "object" || Array.isArray(execution.acceptance[field])) {
        throw new Error(`${source}.execution.acceptance.${field} must be an object`);
      }
    }
    if (typeof execution.acceptance.summaryRecord !== "boolean") {
      throw new Error(`${source}.execution.acceptance.summaryRecord must be boolean`);
    }
  }
  const stepIds = new Set();
  for (const [index, step] of execution.steps.entries()) {
    const location = `${source}.execution.steps[${index}]`;
    requireString(step.id, `${location}.id`);
    if (stepIds.has(step.id)) throw new Error(`${source}: duplicate execution step '${step.id}'`);
    stepIds.add(step.id);
    if (!["sap_read", "skill", "rule"].includes(step.executor)) {
      throw new Error(`${location}.executor is not supported`);
    }
    requireString(step.operation, `${location}.operation`);
    if (["sap_read", "skill"].includes(step.executor) && step.readOnly !== true) {
      throw new Error(`${location} must declare readOnly=true`);
    }
    if (step.when !== undefined) {
      const keys = Object.keys(step.when ?? {}).sort();
      if (keys.join(",") !== "equals,source" || typeof step.when.source !== "string" || typeof step.when.equals !== "boolean") {
        throw new Error(`${location}.when must contain a template source and boolean equals`);
      }
      const match = step.when.source.match(/^\{\{\s*steps\.([a-z][a-z0-9_-]*)\.output(?:\.[A-Za-z0-9_-]+)+\s*\}\}$/);
      if (!match || match[1] === step.id || !stepIds.has(match[1])) throw new Error(`${location}.when must reference a prior step output`);
    }
    if (step.executor === "sap_read") {
      if (!["execute_plan", "execute_get"].includes(step.operation)) {
        throw new Error(`${location}.operation is not allowed for a SAP read Provider`);
      }
      if (!step.request || typeof step.request !== "object" || Array.isArray(step.request)) {
        throw new Error(`${location}.request must be an object`);
      }
      rejectWriteMethods(step.request, location);
      requireODataVersions(step.request, location);
    }
  }
}

function rejectMalformedTemplates(value, location) {
  if (Array.isArray(value)) {
    value.forEach((child, index) => rejectMalformedTemplates(child, `${location}[${index}]`));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      rejectMalformedTemplates(child, `${location}.${key}`);
    }
    return;
  }
  if (typeof value !== "string" || (!value.includes("{{") && !value.includes("}}"))) return;
  const remainder = value.replace(/\{\{\s*[^{}]+?\s*\}\}/g, "");
  if (remainder.includes("{{") || remainder.includes("}}")) {
    throw new Error(`${location} contains a malformed template expression`);
  }
}

function validateSchemaDefault(value, schema, source) {
  if (schema.type === "string") {
    if (typeof value !== "string") throw new Error(`${source}.default must be a string`);
    if (Number.isInteger(schema.minLength) && value.length < schema.minLength) {
      throw new Error(`${source}.default is shorter than minLength`);
    }
    if (Number.isInteger(schema.maxLength) && value.length > schema.maxLength) {
      throw new Error(`${source}.default is longer than maxLength`);
    }
    if (typeof schema.pattern === "string" && !(new RegExp(schema.pattern).test(value))) {
      throw new Error(`${source}.default does not match pattern`);
    }
    if (schema.format === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      throw new Error(`${source}.default must be an ISO date`);
    }
    return;
  }
  if (schema.type === "integer" && (!Number.isInteger(value))) {
    throw new Error(`${source}.default must be an integer`);
  }
  if (schema.type === "number" && (typeof value !== "number" || !Number.isFinite(value))) {
    throw new Error(`${source}.default must be a number`);
  }
  if (schema.type === "boolean" && typeof value !== "boolean") {
    throw new Error(`${source}.default must be boolean`);
  }
  if (schema.type === "array" && !Array.isArray(value)) {
    throw new Error(`${source}.default must be an array`);
  }
  if (
    schema.type === "object"
    && (!value || typeof value !== "object" || Array.isArray(value))
  ) {
    throw new Error(`${source}.default must be an object`);
  }
  if (typeof value === "number") {
    if (typeof schema.minimum === "number" && value < schema.minimum) {
      throw new Error(`${source}.default is below minimum`);
    }
    if (typeof schema.maximum === "number" && value > schema.maximum) {
      throw new Error(`${source}.default is above maximum`);
    }
  }
}

function rejectWriteMethods(value, location) {
  if (Array.isArray(value)) {
    value.forEach((item) => rejectWriteMethods(item, location));
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (["http_method", "httpMethod"].includes(key) && String(child).toUpperCase() !== "GET") {
      throw new Error(`${location} contains a non-GET SAP operation`);
    }
    rejectWriteMethods(child, location);
  }
}

function requireODataVersions(value, location) {
  if (Array.isArray(value)) {
    value.forEach((item) => requireODataVersions(item, location));
    return;
  }
  if (!value || typeof value !== "object") return;
  if (Object.hasOwn(value, "service_name") && Object.hasOwn(value, "entity_set")) {
    if (!["2.0", "4.0"].includes(value.odata_version)) {
      throw new Error(`${location} service references must declare odata_version 2.0 or 4.0`);
    }
  }
  const forbidden = ["url", "resource_path", "service_root_path", "metadata_path", "headers", "authorization", "sap_client"];
  if (forbidden.some((key) => Object.hasOwn(value, key))) {
    throw new Error(`${location} contains forbidden transport fields`);
  }
  Object.values(value).forEach((child) => requireODataVersions(child, location));
}

export function loadAgentCatalog(root = agentsRoot, { includeInactive = true } = {}) {
  if (!existsSync(root)) throw new Error(`Agent root does not exist: ${root}`);

  const topLevelDirectories = readdirSync(root, { withFileTypes: true }).filter((entry) => entry.isDirectory());
  for (const directory of topLevelDirectories) {
    if (!MODULES.includes(directory.name)) throw new Error(`agents/${directory.name}: unsupported module directory`);
  }

  const records = [];
  const slugs = new Set();
  for (const moduleName of MODULES) {
    const modulePath = path.join(root, moduleName);
    if (!existsSync(modulePath)) throw new Error(`Required module directory is missing: agents/${moduleName}`);
    const directories = readdirSync(modulePath, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && !entry.name.startsWith(".") && !entry.name.startsWith("_"))
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const directory of directories) {
      const manifestPath = path.join(modulePath, directory.name, "agent.json");
      if (!existsSync(manifestPath)) throw new Error(`Missing manifest: agents/${moduleName}/${directory.name}/agent.json`);
      let agent;
      try {
        agent = JSON.parse(readFileSync(manifestPath, "utf8"));
      } catch (error) {
        throw new Error(`agents/${moduleName}/${directory.name}/agent.json: invalid JSON (${error.message})`);
      }
      validateAgent(agent, moduleName, directory.name, `agents/${moduleName}/${directory.name}/agent.json`);
      const publicationPath = path.join(modulePath, directory.name, "publication.json");
      if (!includeInactive && existsSync(publicationPath)) {
        let publication;
        try {
          publication = JSON.parse(readFileSync(publicationPath, "utf8"));
        } catch (error) {
          throw new Error(`agents/${moduleName}/${directory.name}/publication.json: invalid JSON (${error.message})`);
        }
        const lifecycleState = publication.lifecycle_state ?? publication.state;
        if (!["active", "inactive"].includes(lifecycleState)) {
          throw new Error(`agents/${moduleName}/${directory.name}/publication.json: lifecycle state must be active or inactive`);
        }
        if (lifecycleState === "inactive") continue;
      }
      if (slugs.has(agent.slug)) throw new Error(`Duplicate agent slug: ${agent.slug}`);
      slugs.add(agent.slug);
      records.push(agent);
    }
  }
  return records;
}

export function generateAgentCatalog(root = agentsRoot, target = outputPath) {
  const records = loadAgentCatalog(root, { includeInactive: false });
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(
    target,
    `// Generated by scripts/generate-agent-catalog.mjs. Do not edit directly.\n` +
      `import type { AgentDefinition } from "../lib/types";\n\n` +
      `export const agents = ${JSON.stringify(records, null, 2)} as const satisfies readonly AgentDefinition[];\n`,
    "utf8",
  );
  return records;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const records = generateAgentCatalog();
  console.log(`Generated ${records.length} Agent record(s) across ${MODULES.length} SAP modules.`);
}
