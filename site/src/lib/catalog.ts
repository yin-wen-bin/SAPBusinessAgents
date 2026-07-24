import { agents } from "../generated/agents";
import { MODULES, type AgentDefinition, type SapModule } from "./types";

export function getAgents(): AgentDefinition[] {
  return [...agents] as AgentDefinition[];
}

export function moduleCounts(records = getAgents()): Record<SapModule, number> {
  const counts = Object.fromEntries(MODULES.map((moduleName) => [moduleName, 0])) as Record<SapModule, number>;
  for (const agent of records) counts[agent.module] += 1;
  return counts;
}
