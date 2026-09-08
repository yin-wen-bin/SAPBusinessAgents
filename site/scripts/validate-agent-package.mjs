import { readFileSync } from "node:fs";
import { validateAgent } from "./generate-agent-catalog.mjs";

try {
  const pkg = JSON.parse(readFileSync(0, "utf8"));
  const agent = pkg.manifest;
  validateAgent(agent, agent.module, agent.slug, "publication");
  if (!pkg.readme?.trim()) throw new Error("Missing README");
  if (!Object.hasOwn(pkg.files ?? {}, agent.validation.reportPath)) throw new Error("Missing acceptance report");
  if (agent.managedRule && !pkg.rules?.trim()) throw new Error("Missing rules");
  process.stdout.write("Package catalog validation passed\n");
} catch {
  process.stderr.write("agent_package_invalid\n");
  process.exitCode = 1;
}
