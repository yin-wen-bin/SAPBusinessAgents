import process from "node:process";
import { deriveAgentPresentation } from "./agent-presentation.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;
try {
  const payload = JSON.parse(input || "{}");
  process.stdout.write(JSON.stringify(deriveAgentPresentation(payload.manifest ?? payload)));
} catch (error) {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
}
