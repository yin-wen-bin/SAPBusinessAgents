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
  executionStepIds: string[];
}

export interface ExecutionInputProperty {
  type: "string" | "number" | "integer" | "boolean" | "object" | "array" | Array<"string" | "number" | "integer" | "boolean" | "object" | "array" | "null">;
  title?: LocalizedText;
  description?: LocalizedText;
  placeholder?: LocalizedText;
  default?: string | number | boolean;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
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
  numericOrderPairs?: Array<{
    lower: string;
    upper: string;
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
  verdict: "PASS" | "PARTIAL" | "FAIL" | "BLOCKED" | "NOT_TESTED";
  testedAt: string;
  evidenceScope: "complete" | "partial" | "bounded";
  providers: string[];
  summary: LocalizedText;
  reportPath: string;
  executable?: boolean;
  caseId?: string;
  baselineRuntime?: "codex_app_direct_sap";
  usedSapBusinessAgentsForBaseline?: false;
  codexDirectBaselineHash?: string;
  freeQueryHash?: string;
  adjudicatedResultHash?: string;
  fixedAgentHash?: string;
  comparisonHash?: string;
  freeQueryComparison?: "MATCH" | "MISMATCH" | "BLOCKED" | "NOT_TESTED";
  fixedAgentComparison?: "MATCH" | "MISMATCH" | "BLOCKED" | "NOT_TESTED";
  blockingLimitations?: string[];
  focusedReplay?: {
    runId: string;
    testedAt: string;
    technicalStatus: "completed" | "inconclusive" | "failed";
    businessStatus: string;
    sourceComplete: boolean;
    businessComplete: boolean;
    blockedFindings: number;
    missingEvidence: string[];
    resultHash: string;
  };
}

export interface AgentAcceptance {
  schemaVersion?: "2.0";
  comparisonMode: "business_semantic";
  businessKeys: string[];
  facts: string[];
  metrics: string[];
  decimalFields?: string[];
  decimalMetricIds?: string[];
  currencyFields?: string[];
  unitFields?: string[];
  dateFields?: string[];
  codeSetFields?: string[];
  zeroPadFields?: Record<string, number>;
  booleanFields?: string[];
  inputDefaults?: Record<string, unknown>;
  constantDefaults?: Record<string, unknown>;
  fieldAliases?: Record<string, unknown>;
  fieldExtractors?: Record<string, unknown>;
  currencyFromDecimal?: Record<string, unknown>;
  valueMappings?: Record<string, unknown>;
  metricValueMappings?: Record<string, unknown>;
  limitationKeywords?: Record<string, string[]>;
  summaryRecord?: boolean;
  currencyAndUnitPolicy: "compare_only_when_same_or_conversion_validated";
  requiredLimitations: string[];
  businessStatusFromMetric?: Record<string, unknown>;
  limitationsFromMetrics?: Record<string, Record<string, string>>;
  blankValueKeywords?: Record<string, unknown>;
  blockingLimitations?: string[];
  ignoredNoticeKeywords?: string[];
  zeroFactWhenMetricZero?: Record<string, string>;
  recordScope?: string;
  metricDefinitions?: Record<string, string>;
  businessStatusDefinition?: string;
  businessStatusFromAnyPositiveMetric?: {
    metrics?: string[];
    positive?: string;
    zero?: string;
  };
  compositeBlankFields?: string[];
  nonBlockingObservationCodes?: string[];
  testDataQualificationDefinition?: string;
  compositeKeyParts?: Record<string, Array<{
    name: string;
    aliases?: string[];
  }>>;
}

export interface AgentExecution {
  mode: "deterministic";
  timeoutSeconds?: number;
  inputSchema: ExecutionInputSchema;
  outputSchema?: ExecutionInputSchema;
  outputMapping?: Record<string, unknown>;
  steps: AgentExecutionStep[];
  acceptance: AgentAcceptance;
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
