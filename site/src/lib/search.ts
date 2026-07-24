import Fuse from "fuse.js";
import type { IFuseOptions } from "fuse.js";
import type { AgentSearchItem, SapModule } from "./types";

const FUSE_OPTIONS: IFuseOptions<AgentSearchItem> = {
  threshold: 0.32,
  ignoreLocation: true,
  minMatchCharLength: 2,
  keys: [
    { name: "title", weight: 0.28 },
    { name: "transactions", weight: 0.18 },
    { name: "tags", weight: 0.15 },
    { name: "summary", weight: 0.14 },
    { name: "workflowTerms", weight: 0.12 },
    { name: "slug", weight: 0.07 },
    { name: "sapModules", weight: 0.04 },
    { name: "module", weight: 0.02 },
  ],
};

export function searchAgents(
  records: AgentSearchItem[],
  query: string,
  moduleName: SapModule | "all" = "all",
): AgentSearchItem[] {
  const moduleRecords = moduleName === "all" ? records : records.filter((record) => record.module === moduleName);
  const normalizedQuery = query.trim();
  if (!normalizedQuery) return moduleRecords;
  return new Fuse(moduleRecords, FUSE_OPTIONS).search(normalizedQuery).map((result) => result.item);
}
