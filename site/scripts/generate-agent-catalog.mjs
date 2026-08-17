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

export function validateAgent(agent, expectedModule, expectedSlug, source) {
  if (![1, 2].includes(agent.schemaVersion)) throw new Error(`${source}: unsupported schemaVersion`);
  if (!SLUG_PATTERN.test(expectedSlug)) throw new Error(`${source}: directory must use a lowercase kebab-case slug`);
  if (agent.slug !== expectedSlug) throw new Error(`${source}: slug must match directory '${expectedSlug}'`);
  if (agent.module !== expectedModule) throw new Error(`${source}: module must match directory '${expectedModule}'`);
  requireLocalized(agent.title, `${source}.title`);
  requireLocalized(agent.summary, `${source}.summary`);
  for (const field of ["status", "version", "owner"]) requireString(agent[field], `${source}.${field}`);
  for (const field of ["tags", "sapModules", "transactions", "tables", "systems"]) requireList(agent[field], `${source}.${field}`);
  for (const field of ["inputs", "outputs", "guardrails"]) {
    requireList(agent[field]?.zh, `${source}.${field}.zh`);
    requireList(agent[field]?.en, `${source}.${field}.en`);
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
  if (agent.schemaVersion === 2) validateExecution(agent.execution, source);
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
  requireList(execution.steps, `${source}.execution.steps`);
  const stepIds = new Set();
  for (const [index, step] of execution.steps.entries()) {
    const location = `${source}.execution.steps[${index}]`;
    requireString(step.id, `${location}.id`);
    if (stepIds.has(step.id)) throw new Error(`${source}: duplicate execution step '${step.id}'`);
    stepIds.add(step.id);
    if (!["sap_read", "sapclaw", "skill", "rule"].includes(step.executor)) {
      throw new Error(`${location}.executor is not supported`);
    }
    requireString(step.operation, `${location}.operation`);
    if (["sap_read", "sapclaw", "skill"].includes(step.executor) && step.readOnly !== true) {
      throw new Error(`${location} must declare readOnly=true`);
    }
    if (["sap_read", "sapclaw"].includes(step.executor)) {
      if (!["execute_plan", "execute_get"].includes(step.operation)) {
        throw new Error(`${location}.operation is not allowed for a SAP read Provider`);
      }
      if (!step.request || typeof step.request !== "object" || Array.isArray(step.request)) {
        throw new Error(`${location}.request must be an object`);
      }
      rejectWriteMethods(step.request, location);
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

export function loadAgentCatalog(root = agentsRoot) {
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
      if (slugs.has(agent.slug)) throw new Error(`Duplicate agent slug: ${agent.slug}`);
      slugs.add(agent.slug);
      records.push(agent);
    }
  }
  return records;
}

export function generateAgentCatalog(root = agentsRoot, target = outputPath) {
  const records = loadAgentCatalog(root);
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
