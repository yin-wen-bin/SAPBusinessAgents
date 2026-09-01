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
  default?: string | number | boolean | null | unknown[] | Record<string, unknown>;
  "x-sapba-server-default"?: boolean;
  "x-sapba-sap-identifier"?: boolean;
  "x-sapba-input-normalization"?: "uppercase" | "preserve";
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  uniqueItems?: boolean;
  minimum?: number;
  exclusiveMinimum?: number;
  maximum?: number;
  pattern?: string;
  format?: string;
  enum?: Array<string | number | boolean | null>;
  const?: string | number | boolean | null;
  items?: ExecutionInputProperty;
  properties?: Record<string, ExecutionInputProperty>;
  required?: string[];
  additionalProperties?: boolean;
  deprecated?: boolean;
  "x-sapba-workflow-only"?: boolean;
  "x-sapba-display"?: {
    visible?: boolean;
    format?: "text" | "enum" | "enum_list" | "status";
    labels?: Record<string, LocalizedText>;
  };
}

export interface ExecutionInputSchema {
  type: "object";
  properties: Record<string, ExecutionInputProperty>;
  required?: string[];
  dependentRequired?: Record<string, string[]>;
  oneOf?: Array<{
    properties?: Record<string, Partial<ExecutionInputProperty>>;
    required?: string[];
    not?: { required?: string[] };
  }>;
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
  acceptanceMode?: "three_stage" | "deterministic_runtime";
  caseId?: string;
  baselineRuntime?: "codex_app_direct_sap" | "sapclaw_runtime";
  runtimeCaseIds?: string[];
  workflowRunIds?: string[];
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
  blankBusinessKeyFields?: string[];
  blockingLimitations?: string[];
  ignoredNoticeKeywords?: string[];
  zeroFactWhenMetricZero?: Record<string, string>;
  recordScope?: string;
  metricDefinitions?: Record<string, string>;
  factDefinitions?: Record<string, string>;
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
  forEach?: {
    source: WorkflowSource;
    groupBy?: Record<string, string>;
    maxItems?: number;
    maxConcurrency?: number;
    onItemError?: "collect_inconclusive";
  };
  runIf?: {
    source: WorkflowSource;
    operator: "non_empty";
  };
  onSkip?: {
    reasonCode: string;
    outputs: Record<string, unknown>;
  };
}

export interface WorkflowSource {
  scope: "workflow_input" | "node_output" | "constant" | "iteration_item";
  nodeId?: string;
  port?: string;
  pointer?: string;
  value?: unknown;
}

export interface WorkflowConnectionDefinition {
  from: WorkflowSource;
  to: { nodeId: string; port: string };
  transform?: { type: string; [key: string]: unknown };
}

export interface WorkflowDefinition {
  schemaVersion: 1 | 2;
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
  outputs: Array<{
    name: string;
    source?: WorkflowSource;
    transform?: { type: string };
    aggregate?: {
      operator: "status_precedence" | "all_true" | "collect";
      sources: WorkflowSource[];
      precedence?: string[];
    };
  }>;
  policies: { onInconclusive: "continue_if_required_outputs_present" };
}

export interface WorkflowCompositionPort {
  name: string;
  type: string;
  description: LocalizedText;
  required: boolean;
}

export interface WorkflowCompositionGap {
  gap_id: string;
  stage_id: string;
  title: LocalizedText;
  description: LocalizedText;
  required_inputs: WorkflowCompositionPort[];
  required_outputs: WorkflowCompositionPort[];
  guardrails: LocalizedList;
  acceptance: LocalizedText;
  status: string;
  agent_draft_id?: string | null;
}

export interface WorkflowCompositionStage {
  id: string;
  capability: LocalizedText;
  agent_id?: string | null;
  confidence: "high" | "medium" | "low";
  reason: LocalizedText;
  bindings: Array<Record<string, unknown>>;
  requested_outputs: string[];
  runtime_requested_outputs?: string[];
}

export interface WorkflowComposition {
  requirement?: string;
  locale?: Locale;
  intent?: LocalizedText;
  catalog_digest?: string;
  compiler_version?: number;
  stages?: WorkflowCompositionStage[];
  gaps?: WorkflowCompositionGap[];
  validation_defaults?: Record<string, unknown>;
  clarification_question?: string;
  clarification_history?: Array<{ question: string; answer: string }>;
  reconciling?: boolean;
  proposal_snapshot?: Record<string, unknown>;
  conversation?: {
    current_turn?: number;
    status?: string;
    requires_design_acceptance?: boolean;
    accepted_design?: Record<string, unknown> | null;
    accepted_validation?: Record<string, unknown> | null;
    runtime_snapshot?: Record<string, unknown>;
    pending_feedback?: Record<string, unknown> | null;
  };
  output_normalization?: {
    dismissed_requested_outputs?: Array<{
      stage_id: string;
      port: string;
      reason_code: string;
    }>;
  };
  error?: {
    code?: string;
    message?: string;
    type?: string;
    detail?: { node_id?: string; port?: string } | null;
  } | null;
}

export interface AgentDefinition {
  schemaVersion: number;
  kind?: "platform_assistant";
  assistant?: {
    type: "role_matching";
    runtimeCapability: "role_matching";
    composable: false;
    localFileAccess: "read_only_user_selected";
  };
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
