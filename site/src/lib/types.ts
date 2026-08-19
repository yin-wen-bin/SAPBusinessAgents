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

export interface WorkflowSapScope {
  modules: string[];
  transactions: string[];
  tables: string[];
}

export interface WorkflowStep {
  id: string;
  title: LocalizedText;
  description: LocalizedText;
  operations?: LocalizedList;
  sapScope?: WorkflowSapScope;
  tools: WorkflowTool[];
}

export interface ExecutionInputProperty {
  type: "string" | "number" | "integer" | "boolean" | "object" | "array";
  title?: LocalizedText;
  description?: LocalizedText;
  placeholder?: LocalizedText;
  default?: string | number | boolean;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  format?: "date";
}

export interface ExecutionInputSchema {
  type: "object";
  properties: Record<string, ExecutionInputProperty>;
  required?: string[];
  additionalProperties?: boolean;
  dateRangePairs?: Array<{
    from: string;
    to: string;
    label?: LocalizedText;
    maxDays?: number;
  }>;
}

export interface AgentExecutionStep {
  id: string;
  executor: "sap_read" | "skill" | "rule";
  operation: string;
  readOnly?: boolean;
  request?: Record<string, unknown>;
  inputMapping?: Record<string, unknown>;
  skillId?: string;
  failurePolicy?: "fail_run" | "record_gap";
  when?: { source: string; equals: boolean };
}

export interface AgentValidation {
  verdict: "PASS" | "PARTIAL" | "FAIL" | "BLOCKED";
  testedAt: string;
  evidenceScope: "complete" | "partial" | "bounded";
  providers: string[];
  summary: LocalizedText;
  reportPath: string;
}

export interface AgentExecution {
  mode: "deterministic";
  timeoutSeconds?: number;
  inputSchema: ExecutionInputSchema;
  outputSchema?: ExecutionInputSchema;
  outputMapping?: Record<string, unknown>;
  steps: AgentExecutionStep[];
}

export interface WorkflowNodeDefinition {
  id: string;
  agentId: string;
  agentVersion?: string;
  agentDigest?: string;
  position?: { x: number; y: number };
}

export interface WorkflowSource {
  scope: "workflow_input" | "node_output" | "constant";
  nodeId?: string;
  port?: string;
  value?: unknown;
}

export interface WorkflowConnectionDefinition {
  from: WorkflowSource;
  to: { nodeId: string; port: string };
  transform?: { type: string; [key: string]: unknown };
}

export interface WorkflowDefinition {
  schemaVersion: 1;
  id: string;
  version: string;
  title: LocalizedText;
  description: LocalizedText;
  mode: "deterministic";
  readOnly: true;
  inputSchema: ExecutionInputSchema;
  outputSchema: ExecutionInputSchema;
  nodes: WorkflowNodeDefinition[];
  connections: WorkflowConnectionDefinition[];
  outputs: Array<{ name: string; source: WorkflowSource; transform?: { type: string } }>;
  policies: { onInconclusive: "continue_if_required_outputs_present" };
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
  execution?: AgentExecution;
  validation?: AgentValidation;
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
  odataVersions: string[];
  workflowTerms: string[];
  href: string;
}
