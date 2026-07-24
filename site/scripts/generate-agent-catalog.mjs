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
  if (agent.schemaVersion !== 1) throw new Error(`${source}: unsupported schemaVersion`);
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
      .filter((entry) => entry.isDirectory())
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
