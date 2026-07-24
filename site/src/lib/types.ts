export const MODULES = ["Common", "FI", "CO", "SD", "MM", "PP"] as const;

export type SapModule = (typeof MODULES)[number];
export type Locale = "zh" | "en";

export interface LocalizedText {
  zh: string;
  en: string;
}

export interface LocalizedList {
  zh: string[];
  en: string[];
}

export interface WorkflowTool {
  name: string;
  kind: string;
  purpose: LocalizedText;
}

export interface WorkflowStep {
  id: string;
  title: LocalizedText;
  description: LocalizedText;
  tools: WorkflowTool[];
}

export interface AgentDefinition {
  schemaVersion: number;
  slug: string;
  module: SapModule;
  title: LocalizedText;
  summary: LocalizedText;
  status: string;
  version: string;
  owner: string;
  tags: string[];
  sapModules: string[];
  transactions: string[];
  tables: string[];
  systems: string[];
  inputs: LocalizedList;
  outputs: LocalizedList;
  guardrails: LocalizedList;
  workflow: WorkflowStep[];
}

export interface AgentSearchItem {
  id: string;
  slug: string;
  module: SapModule;
  title: string;
  summary: string;
  tags: string[];
  transactions: string[];
  sapModules: string[];
  workflowTerms: string[];
  href: string;
}
