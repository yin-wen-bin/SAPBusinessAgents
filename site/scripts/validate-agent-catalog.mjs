import { loadAgentCatalog, MODULES } from "./generate-agent-catalog.mjs";

try {
  const records = loadAgentCatalog();
  console.log(`Validated ${records.length} Agent manifest(s) across ${MODULES.length} SAP modules.`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
