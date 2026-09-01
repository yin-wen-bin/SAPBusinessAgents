import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type {
  AgentDefinition,
  ExecutionInputProperty,
  ExecutionInputSchema,
  Locale,
  WorkflowConnectionDefinition,
  WorkflowComposition,
  WorkflowDefinition,
} from "../lib/types";

type Draft = {
  draft_id: string;
  status: string;
  revision: number;
  workflow: WorkflowDefinition;
  validation_run_id?: string | null;
  validation: Record<string, unknown>;
  composition: WorkflowComposition;
};

type BuilderProps = { apiBase: string; locale: Locale; runPath: string; askPath: string; onPublished?: (workflowId: string) => void };
type AgentNodeData = { agent: AgentDefinition; locale: Locale };
type ValidationFeedback = { kind: "progress" | "error"; text: string };
type WizardStep = "compose" | "review" | "validate" | "publish";
type ValidationExpectation = {
  output: string;
  operator: "equals" | "one_of" | "exists" | "non_empty" | "decimal_within";
  expected?: unknown;
  tolerance?: string;
};
type ValidationReport = {
  phase: "running" | "completed";
  run_id: string;
  workflow_revision: number;
  workflow_hash: string;
  started_at?: string | null;
  completed_at?: string | null;
  verdict: "pending" | "pass" | "inconclusive" | "fail" | "blocked";
  normalized_input?: Record<string, unknown>;
  sample_source?: "user" | "auto_discovered";
  preflight_review?: Record<string, unknown>;
  automatic_checks?: Array<Record<string, unknown>>;
  user_expectations?: Array<Record<string, unknown>>;
  node_results?: Array<Record<string, unknown>>;
  required_output_checks?: Array<Record<string, unknown>>;
  business_result?: Record<string, unknown>;
  completeness?: Record<string, unknown>;
  evidence_gaps?: Array<Record<string, unknown>>;
  errors?: Array<Record<string, unknown>>;
  artifacts?: Array<{ name: string; media_type: string }>;
  report_digest?: string;
  progress?: Record<string, unknown>;
};
type RunSnapshot = {
  run_id: string;
  status: string;
  progress?: Record<string, unknown>;
};
type WorkflowConversationTurn = {
  turn: number;
  parent_turn?: number | null;
  kind: string;
  status: string;
  user_message?: string | null;
  feedback_type?: string | null;
  action?: string | null;
  decision?: Record<string, unknown>;
  base_revision?: number | null;
  result_revision?: number | null;
  workflow_hash?: string | null;
  diff?: Array<Record<string, unknown>>;
  validation_run_id?: string | null;
  validation_report_digest?: string | null;
  created_at: string;
  completed_at?: string | null;
};
type WorkflowConversation = {
  draft_id: string;
  current_turn: number;
  current_workflow_hash: string;
  status: string;
  accepted_design?: Record<string, unknown> | null;
  accepted_validation?: Record<string, unknown> | null;
  runtime_snapshot?: Record<string, unknown>;
  turn_limit: number;
  turns: WorkflowConversationTurn[];
};

const WORKFLOW_REVIEW_POLICY_VERSION = 2;

const labels = {
  zh: {
    title: "用一句话生成工作流",
    lead: "描述业务目标，当前默认 Agent Runtime 会从当前仓库的可执行 Agent 中自动选择、排序并连接输入输出。",
    requirement: "你希望完成什么业务任务？",
    requirementPlaceholder: "例如：检查指定采购订单从收货、发票到清账的完整状态，并输出缺失环节和下一步。",
    compose: "生成工作流草稿",
    composing: "正在匹配 Agent 并编译工作流…",
    draftReady: "草稿已生成",
    draftReadyDetail: "下一步请检查业务步骤、输入输出映射和条件分支。",
    draftReadyWithGaps: "草稿已生成，但仍存在能力缺口",
    draftStepUnit: "个业务步骤",
    normalizedOutputs: "平台已清理非业务终态输出",
    normalizedOutputsHelp: "这些字段仍会作为Agent输入，但不会被误当成工作流业务结果或跳过终态。",
    compositionFailed: "草稿生成未完成",
    unsafeSkipOutput: "节点 {node} 的输出 {port} 无法生成安全、真实的跳过值。",
    retryComposition: "重新生成草稿",
    retryingComposition: "正在使用compiler v4重新生成草稿…",
    advanced: "高级编辑",
    hideAdvanced: "收起高级编辑",
    manual: "从空白画布开始",
    intent: "理解到的业务目标",
    route: "自动编排结果",
    matchedAgent: "已匹配 Agent",
    missingAgent: "缺口 Agent",
    gapTitle: "还缺少以下 Agent",
    gapHelp: "缺口解决前不能验证或发布。可转到自由查询，先完成一次只读查询，再保存为待审核 Agent 草稿。",
    createGapAgent: "用自由查询创建此 Agent",
    gapDraft: "Agent 草稿",
    awaitingCatalog: "草稿尚未进入可执行目录；完成审核、真机验证和发布后，返回此页会自动重新匹配。",
    clarify: "Agent Runtime 需要确认一个关键信息",
    clarifyPlaceholder: "请直接回答这个问题",
    continueCompose: "继续生成",
    reconciled: "已检查当前 Agent 目录",
    catalogOnly: "只会采用当前仓库中状态为可执行的 Agent，并固定版本与摘要。",
    agents: "可用 Agents",
    save: "保存草稿",
    validate: "Agent Runtime 真机验证",
    validating: "正在进行设计预审；通过后才会发现真机样本并启动验证…",
    discovering: "正在通过只读 SAP 查询自动发现真机样本并启动验证…",
    autoDiscoverFailed: "未能自动发现以下必填输入，请手工填写后重试",
    publish: "发布固定工作流",
    selectNode: "选择一个节点配置输入映射",
    mapping: "输入映射",
    metadata: "工作流信息",
    workflowId: "工作流标识",
    workflowName: "工作流名称",
    validationInputs: "真机验证输入",
    autoDiscover: "必填业务主键留空时，系统会通过有界、只读的 SAP 查询自动发现一个真机样本；也可以手工填写以验证指定业务数据。",
    required: "必填",
    noIssues: "尚未发现结构或运行问题。",
    validationDetail: "验证说明",
    constant: "常量",
    status: "草稿状态",
    openRun: "查看验证过程",
    remove: "移除节点",
    steps: ["生成草稿", "检查工作流", "真机验证", "发布工作流"],
    nextReview: "下一步：检查工作流",
    nextValidation: "设计符合预期",
    nextPublish: "已阅读报告，进入发布",
    conversationTitle: "工作流对话记录",
    conversationHelp: "每轮反馈都会保留；Runtime修改会生成新的工作流修订，旧版本和旧验证报告不会被覆盖。",
    feedbackTitle: "这个工作流还需要修改吗？",
    feedbackPlaceholder: "请说明不符合预期的地方，以及你希望如何调整。",
    sendFeedback: "提交反馈",
    feedbackPlanning: "Runtime正在理解反馈并生成受控修订…",
    feedbackClarification: "Runtime需要补充一个信息",
    feedbackInput: "补充反馈信息",
    designAccepted: "当前工作流设计已确认，可以进行真机验证。",
    acceptDesign: "设计符合预期",
    reviseDesign: "需要修改",
    validationNeedsRevision: "验证结果需要修正",
    acceptValidation: "验证结果符合预期",
    validationAccepted: "当前验证报告已确认，可以进入发布。",
    newWorkflowIntent: "这是另一个工作流",
    undoTurn: "撤销本轮修改",
    feedbackCategoriesReview: ["业务目标不对", "Agent或步骤不对", "输入输出映射不对", "条件分支不对", "最终输出或完整性不对", "名称或说明不对"],
    feedbackCategoryValuesReview: ["goal_scope", "stage_or_agent", "mapping", "condition", "output_or_completeness", "presentation"],
    feedbackCategoriesValidate: ["测试数据不对", "预期结果不对", "工作流逻辑不对", "Agent能力不足", "验证报告看不懂"],
    feedbackCategoryValuesValidate: ["validation_input", "validation_expectation", "output_or_completeness", "agent_capability", "presentation"],
    turnKinds: { initial: "初始需求", clarification: "补充说明", feedback: "设计反馈", manual_edit: "手工修改", catalog_reconcile: "目录重编译", validation: "真机验证", validation_feedback: "验证反馈", undo: "撤销修订" },
    changedRevision: "生成工作流修订",
    reusedDesign: "工作流定义未改变",
    readSapAgain: "重新执行真机验证",
    noSapRead: "未读取SAP",
    back: "上一步",
    preflightTitle: "设计预审",
    preflightPassed: "设计预审通过，可以开始真机验证",
    validationRunning: "真机验证进行中",
    validationPassed: "真机验证完成 · 通过",
    validationInconclusive: "真机验证完成 · 存在完整性缺口",
    validationFailed: "真机验证完成 · 未通过",
    validationBlocked: "设计预审阻止了真机验证",
    preflightStale: "设计预审规则已更新，请重新预审",
    preflightStaleHelp: "该结果来自旧版预审策略。重新预审将先完成设计门禁，再进行样本发现和 SAP 查询。",
    preflightBlockedTitle: "设计预审未通过",
    preflightBlockedHelp: "以下问题必须解决后才能读取SAP并开始真机验证。",
    preflightNoSap: "未启动样本发现、SAP查询或验证运行。",
    retryPreflight: "重新进行设计预审",
    returnToReview: "返回检查工作流",
    issueNode: "节点",
    issuePort: "端口",
    reportTitle: "真机验证测试报告",
    reportHelp: "以下结论由平台根据真实运行、节点输出、只读审计和完整性标志确定，不是Agent Runtime自行宣布的通过结果。",
    automaticChecks: "平台自动检查",
    expectations: "关键业务预期（可选）",
    expectationsHelp: "选择一个工作流终端输出并声明预期。填写后，该断言必须满足才能发布。",
    addExpectation: "添加预期",
    noExpectations: "本次没有设置自定义业务预期。",
    expectedOutput: "工作流输出",
    expectedOperator: "比较方式",
    expectedValue: "预期值",
    tolerance: "容差",
    nodeResults: "节点执行结果",
    requiredOutputs: "必需输出检查",
    evidenceGaps: "完整性缺口",
    noEvidenceGaps: "没有发现完整性缺口。",
    normalizedInput: "实际验证输入",
    sampleSource: "样本来源",
    autoDiscovered: "系统自动发现",
    userProvided: "用户提供",
    reportDigest: "报告摘要",
    publishSummary: "发布确认",
    publishReady: "验证报告允许进入发布。",
    publishBlocked: "当前验证结论不允许发布，请返回修改或重新验证。",
    acknowledgeDetailed: "本次真机验证存在以下证据完整性缺口。我理解这些缺口可能影响业务结论，仍决定发布该固定工作流。",
    acknowledgementHelp: "此确认只授权带缺口发布，不会把结果改为通过、补齐SAP证据或隐藏限制。",
    downloadJson: "下载JSON验证报告",
    downloadMarkdown: "下载Markdown验证报告",
    statusLabel: "验证结论",
    technicalResult: "技术验证",
    businessResult: "SAP业务结果",
    completenessResult: "数据完整性",
    expectationResult: "用户预期",
    noValidation: "尚未执行真机验证。",
    columns: { node: "节点", agent: "Agent", state: "状态", childRun: "子运行", duration: "耗时", business: "业务状态", source: "查询完整", evidence: "证据完整", tools: "工具调用", result: "结果" },
  },
  en: {
    title: "Generate a workflow from one request",
    lead: "Describe the business outcome. The current default Agent Runtime selects executable repository Agents, orders them, and wires compatible inputs and outputs.",
    requirement: "What business task should this workflow complete?",
    requirementPlaceholder: "Example: check a purchase order from goods receipt through invoice and clearing, then report missing stages and next actions.",
    compose: "Generate workflow draft",
    composing: "Matching Agents and compiling the workflow…",
    draftReady: "Workflow draft generated",
    draftReadyDetail: "Next, review the business steps, input/output mappings, and conditional branches.",
    draftReadyWithGaps: "The draft was generated with capability gaps",
    draftStepUnit: "business step(s)",
    normalizedOutputs: "Non-business terminal outputs were removed",
    normalizedOutputsHelp: "These fields are still passed as Agent inputs, but are not treated as workflow business results or skipped terminal values.",
    compositionFailed: "Workflow draft generation did not complete",
    unsafeSkipOutput: "Node {node} cannot derive a safe and truthful skipped value for output {port}.",
    retryComposition: "Generate draft again",
    retryingComposition: "Regenerating the draft with compiler v4…",
    advanced: "Advanced editor",
    hideAdvanced: "Hide advanced editor",
    manual: "Start with a blank canvas",
    intent: "Interpreted business outcome",
    route: "Composed route",
    matchedAgent: "Matched Agent",
    missingAgent: "Missing Agent",
    gapTitle: "These Agents are still missing",
    gapHelp: "Validation and publishing stay blocked until every gap is resolved. Use a read-only free query, then save the result as an Agent draft for review.",
    createGapAgent: "Create this Agent with free query",
    gapDraft: "Agent draft",
    awaitingCatalog: "The draft is not executable yet. After review, live validation, and publishing, return here to trigger automatic matching.",
    clarify: "The Agent Runtime needs one key detail",
    clarifyPlaceholder: "Answer this question directly",
    continueCompose: "Continue",
    reconciled: "Current Agent catalog checked",
    catalogOnly: "Only executable repository Agents are selected, with version and digest pinned.",
    agents: "Available Agents",
    save: "Save draft",
    validate: "Validate live with Agent Runtime",
    validating: "Running design preflight; live candidate discovery starts only after it passes…",
    discovering: "Discovering a live candidate through a bounded, read-only SAP query and starting validation…",
    autoDiscoverFailed: "A live candidate could not be discovered for these required inputs. Enter them and try again",
    publish: "Publish fixed workflow",
    selectNode: "Select a node to configure its input mappings",
    mapping: "Input mapping",
    metadata: "Workflow details",
    workflowId: "Workflow ID",
    workflowName: "Workflow name",
    validationInputs: "Live validation input",
    autoDiscover: "When a required business key is blank, the system uses a bounded, read-only SAP query to discover one live candidate. You can enter a value to validate specific business data instead.",
    required: "required",
    noIssues: "No structural or runtime issue is currently reported.",
    validationDetail: "Validation detail",
    constant: "Constant",
    status: "Draft status",
    openRun: "Open validation run",
    remove: "Remove node",
    steps: ["Generate draft", "Review workflow", "Live validation", "Publish workflow"],
    nextReview: "Next: review workflow",
    nextValidation: "The design meets my expectations",
    nextPublish: "I reviewed the report — continue",
    conversationTitle: "Workflow conversation",
    conversationHelp: "Every feedback turn is retained. Runtime revisions create a new workflow revision without overwriting earlier designs or validation reports.",
    feedbackTitle: "Does this workflow need another revision?",
    feedbackPlaceholder: "Describe what is wrong and how the workflow should change.",
    sendFeedback: "Send feedback",
    feedbackPlanning: "The Runtime is reviewing feedback and compiling a controlled revision…",
    feedbackClarification: "The Runtime needs one more detail",
    feedbackInput: "Provide feedback detail",
    designAccepted: "The current workflow design is confirmed and can be validated live.",
    acceptDesign: "The design meets my expectations",
    reviseDesign: "Revise the design",
    validationNeedsRevision: "Revise the validation result",
    acceptValidation: "The validation result meets my expectations",
    validationAccepted: "The current validation report is confirmed and can be published.",
    newWorkflowIntent: "This is another workflow",
    undoTurn: "Undo this revision",
    feedbackCategoriesReview: ["Business goal is wrong", "Agent or step is wrong", "Input/output mapping is wrong", "Condition is wrong", "Terminal output or completeness is wrong", "Name or description is wrong"],
    feedbackCategoryValuesReview: ["goal_scope", "stage_or_agent", "mapping", "condition", "output_or_completeness", "presentation"],
    feedbackCategoriesValidate: ["Test data is wrong", "Expected result is wrong", "Workflow logic is wrong", "Agent capability is insufficient", "Validation report is unclear"],
    feedbackCategoryValuesValidate: ["validation_input", "validation_expectation", "output_or_completeness", "agent_capability", "presentation"],
    turnKinds: { initial: "Initial request", clarification: "Clarification", feedback: "Design feedback", manual_edit: "Manual edit", catalog_reconcile: "Catalog reconcile", validation: "Live validation", validation_feedback: "Validation feedback", undo: "Undo revision" },
    changedRevision: "Created workflow revision",
    reusedDesign: "Workflow definition unchanged",
    readSapAgain: "Live validation rerun",
    noSapRead: "SAP was not read",
    back: "Back",
    preflightTitle: "Design preflight",
    preflightPassed: "Design preflight passed; live validation can start",
    validationRunning: "Live validation in progress",
    validationPassed: "Live validation completed · Passed",
    validationInconclusive: "Live validation completed · Completeness gaps remain",
    validationFailed: "Live validation completed · Failed",
    validationBlocked: "Design preflight blocked live validation",
    preflightStale: "The design-preflight policy changed; run the review again",
    preflightStaleHelp: "This result used an older review policy. A new review will complete the design gate before sample discovery or SAP queries.",
    preflightBlockedTitle: "Design preflight did not pass",
    preflightBlockedHelp: "Resolve the following issues before the platform reads SAP or starts live validation.",
    preflightNoSap: "Candidate discovery, SAP queries, and the validation run were not started.",
    retryPreflight: "Run design preflight again",
    returnToReview: "Return to workflow review",
    issueNode: "Node",
    issuePort: "Port",
    reportTitle: "Live validation test report",
    reportHelp: "The platform derives this verdict from the real run, node outputs, read-only audit, and completeness flags. It is not a pass declared by the Agent Runtime.",
    automaticChecks: "Automatic checks",
    expectations: "Key business expectations (optional)",
    expectationsHelp: "Select a terminal workflow output and declare the expected result. Once configured, the assertion must pass before publication.",
    addExpectation: "Add expectation",
    noExpectations: "No custom business expectation was configured for this validation.",
    expectedOutput: "Workflow output",
    expectedOperator: "Operator",
    expectedValue: "Expected value",
    tolerance: "Tolerance",
    nodeResults: "Node execution results",
    requiredOutputs: "Required output checks",
    evidenceGaps: "Completeness gaps",
    noEvidenceGaps: "No completeness gap was found.",
    normalizedInput: "Actual validation input",
    sampleSource: "Sample source",
    autoDiscovered: "Automatically discovered",
    userProvided: "User supplied",
    reportDigest: "Report digest",
    publishSummary: "Publication confirmation",
    publishReady: "The validation report permits publication.",
    publishBlocked: "The current validation verdict cannot be published. Revise or revalidate the workflow.",
    acknowledgeDetailed: "This live validation has the completeness gaps listed below. I understand that they may affect the business conclusion and still choose to publish this fixed workflow.",
    acknowledgementHelp: "This only authorizes publication with gaps. It does not turn the result into a pass, add SAP evidence, or hide limitations.",
    downloadJson: "Download JSON validation report",
    downloadMarkdown: "Download Markdown validation report",
    statusLabel: "Validation verdict",
    technicalResult: "Technical validation",
    businessResult: "SAP business result",
    completenessResult: "Data completeness",
    expectationResult: "User expectations",
    noValidation: "Live validation has not run yet.",
    columns: { node: "Node", agent: "Agent", state: "Status", childRun: "Child run", duration: "Duration", business: "Business status", source: "Source complete", evidence: "Evidence complete", tools: "Tool calls", result: "Result" },
  },
} as const;

function AgentNode({ data, selected }: NodeProps<Node<AgentNodeData>>) {
  const { agent, locale } = data;
  const inputs = Object.keys(agent.execution?.inputSchema.properties ?? {});
  const outputs = Object.keys(agent.execution?.outputSchema?.properties ?? {});
  return (
    <div className={`workflow-agent-node${selected ? " selected" : ""}`}>
      <div className="workflow-agent-node__module">{agent.module}</div>
      <strong>{agent.title[locale]}</strong>
      <small>{agent.slug}</small>
      <div className="workflow-agent-node__ports">
        <div>
          {inputs.map((port) => (
            <div className="workflow-port workflow-port--input" key={port}>
              <Handle type="target" position={Position.Left} id={`in:${port}`} />
              <span>{port}</span>
            </div>
          ))}
        </div>
        <div>
          {outputs.map((port) => (
            <div className="workflow-port workflow-port--output" key={port}>
              <span>{port}</span>
              <Handle type="source" position={Position.Right} id={`out:${port}`} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

function emptyWorkflow(): WorkflowDefinition {
  return {
    schemaVersion: 2,
    id: `workflow-${crypto.randomUUID().slice(0, 8)}`,
    version: "0.1.0",
    title: { zh: "未命名工作流", en: "Untitled workflow" },
    description: { zh: "由固定 Agent 组成的只读工作流。", en: "Read-only workflow composed from fixed Agents." },
    mode: "deterministic",
    readOnly: true,
    inputSchema: { type: "object", properties: {}, required: [], additionalProperties: false },
    outputSchema: { type: "object", properties: {}, required: [], additionalProperties: false },
    nodes: [],
    connections: [],
    outputs: [],
    policies: { onInconclusive: "continue_if_required_outputs_present" },
  };
}

function connectionId(item: WorkflowConnectionDefinition): string {
  const from = item.from.scope === "node_output" ? `${item.from.nodeId}:${item.from.port}` : item.from.scope === "iteration_item" ? `iteration:${item.from.pointer ?? "/"}` : `${item.from.scope}:${item.from.port ?? "value"}`;
  return `${from}->${item.to.nodeId}:${item.to.port}`;
}

export default function WorkflowBuilder({ apiBase, locale, runPath, askPath, onPublished }: BuilderProps) {
  const t = labels[locale];
  const [agents, setAgents] = useState<AgentDefinition[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [requirement, setRequirement] = useState("");
  const [clarificationInput, setClarificationInput] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationFeedback, setValidationFeedback] = useState<ValidationFeedback | null>(null);
  const [acknowledge, setAcknowledge] = useState(false);
  const [validationInputs, setValidationInputs] = useState<Record<string, string>>({});
  const [expectations, setExpectations] = useState<ValidationExpectation[]>([]);
  const [validationReport, setValidationReport] = useState<ValidationReport | null>(null);
  const [liveRun, setLiveRun] = useState<RunSnapshot | null>(null);
  const [activeStep, setActiveStep] = useState<WizardStep>("compose");
  const [conversation, setConversation] = useState<WorkflowConversation | null>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackType, setFeedbackType] = useState("");
  const [feedbackInput, setFeedbackInput] = useState("");
  const [dirty, setDirty] = useState(false);
  const pollTimer = useRef<number | null>(null);
  const validationEvents = useRef<EventSource | null>(null);
  const reconciledDrafts = useRef(new Set<string>());

  const applyDraft = useCallback((value: Draft) => {
    setDraft(value);
    const report = value.validation?.validation_report;
    setValidationReport(
      report && typeof report === "object" ? report as ValidationReport : null,
    );
    if (value.composition?.requirement) setRequirement(value.composition.requirement);
    if (value.composition?.validation_defaults) {
      setValidationInputs((current) => ({
        ...Object.fromEntries(Object.entries(value.composition.validation_defaults ?? {}).map(([key, item]) => [key, item == null ? "" : String(item)])),
        ...current,
      }));
    }
    if (!value.composition?.requirement) setAdvanced(true);
    setDirty(false);
  }, []);

  const refreshConversation = useCallback(async (draftId: string) => {
    const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}/conversation`);
    if (response.ok) setConversation((await response.json()) as WorkflowConversation);
  }, [apiBase]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current != null) window.clearInterval(pollTimer.current);
    pollTimer.current = null;
  }, []);

  const stopValidationEvents = useCallback(() => {
    validationEvents.current?.close();
    validationEvents.current = null;
  }, []);

  const moveToStep = useCallback((step: WizardStep, draftId?: string) => {
    setActiveStep(step);
    if (typeof window === "undefined") return;
    const query = new URLSearchParams(window.location.search);
    query.set("view", "create");
    query.delete("workflow");
    if (draftId) query.set("draft", draftId);
    query.set("step", step);
    history.replaceState({}, "", `${window.location.pathname}?${query.toString()}`);
  }, []);

  const refreshValidation = useCallback(async (value: Draft) => {
    if (!value.validation_run_id) {
      setLiveRun(null);
      setValidationReport(null);
      return;
    }
    const runResponse = await fetch(`${apiBase}/api/runs/${encodeURIComponent(value.validation_run_id)}`);
    if (runResponse.ok) setLiveRun((await runResponse.json()) as RunSnapshot);
    const reportResponse = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(value.draft_id)}/validation-report`);
    if (reportResponse.ok) setValidationReport((await reportResponse.json()) as ValidationReport);
  }, [apiBase]);

  const pollDraft = useCallback((draftId: string) => {
    stopPolling();
    pollTimer.current = window.setInterval(async () => {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}`);
      if (!response.ok) return;
      const value = (await response.json()) as Draft;
      applyDraft(value);
      await refreshConversation(draftId);
      await refreshValidation(value);
      if (
        value.status !== "planning"
        && !["queued", "running", "preflight"].includes(String(value.validation?.phase ?? ""))
        && value.validation?.live_status !== "running"
      ) stopPolling();
    }, 1000);
  }, [apiBase, applyDraft, refreshConversation, refreshValidation, stopPolling]);

  const connectValidationEvents = useCallback((draftId: string, runId: string) => {
    stopValidationEvents();
    const events = new EventSource(`${apiBase}/api/runs/${encodeURIComponent(runId)}/events`);
    validationEvents.current = events;
    const refresh = async () => {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}`);
      if (!response.ok) return;
      const value = (await response.json()) as Draft;
      applyDraft(value);
      await refreshConversation(draftId);
      await refreshValidation(value);
      if (String(value.validation?.phase ?? "") === "completed") stopValidationEvents();
    };
    for (const event of [
      "progress_changed", "workflow_started", "node_started", "node_completed",
      "node_inconclusive", "node_skipped_empty_input", "run_completed", "run_inconclusive",
      "run_failed", "run_cancelled",
    ]) events.addEventListener(event, () => { void refresh(); });
    events.onerror = () => {
      events.close();
      if (validationEvents.current === events) validationEvents.current = null;
      pollDraft(draftId);
    };
  }, [apiBase, applyDraft, pollDraft, refreshConversation, refreshValidation, stopValidationEvents]);

  useEffect(() => {
    void (async () => {
      const response = await fetch(`${apiBase}/api/agents?executable=true`);
      const all = (await response.json()) as AgentDefinition[];
      setAgents(all.filter((agent) => Boolean(agent.execution?.outputSchema)));
      const requested = new URLSearchParams(window.location.search).get("draft");
      const requestedStep = new URLSearchParams(window.location.search).get("step");
      if (["compose", "review", "validate", "publish"].includes(String(requestedStep))) {
        setActiveStep(requestedStep as WizardStep);
      }
      if (requested) {
        const existing = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(requested)}`);
        if (existing.ok) {
          const value = (await existing.json()) as Draft;
          applyDraft(value);
          await refreshConversation(value.draft_id);
          await refreshValidation(value);
          if (value.status === "planning" || ["queued", "running"].includes(String(value.validation?.phase ?? ""))) pollDraft(value.draft_id);
          if (value.validation_run_id && ["queued", "running"].includes(String(value.validation?.phase ?? ""))) {
            connectValidationEvents(value.draft_id, value.validation_run_id);
          }
        }
      }
    })().catch((error) => setMessage(String(error)));
    return () => { stopPolling(); stopValidationEvents(); };
  }, [apiBase, applyDraft, connectValidationEvents, pollDraft, refreshConversation, refreshValidation, stopPolling, stopValidationEvents]);

  useEffect(() => {
    if (!draft || draft.status !== "needs_agents") return;
    const key = `${draft.draft_id}:${draft.composition?.catalog_digest ?? ""}`;
    if (reconciledDrafts.current.has(key)) return;
    reconciledDrafts.current.add(key);
    void fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/reconcile`, { method: "POST" })
      .then(async (response) => {
        if (!response.ok) return;
        const value = (await response.json()) as Draft;
        applyDraft(value);
        if (value.status === "planning") pollDraft(value.draft_id);
      })
      .catch(() => undefined);
  }, [apiBase, applyDraft, draft, pollDraft]);

  const agentMap = useMemo(() => new Map(agents.map((agent) => [agent.slug, agent])), [agents]);
  const nodes = useMemo<Node<AgentNodeData>[]>(() => {
    if (!draft) return [];
    return draft.workflow.nodes.flatMap((item, index) => {
      const agent = agentMap.get(item.agentId);
      if (!agent) return [];
      return [{ id: item.id, type: "agent", position: item.position ?? { x: 80 + index * 360, y: 120 }, data: { agent, locale } }];
    });
  }, [draft, agentMap, locale]);
  const edges = useMemo<Edge[]>(() => {
    if (!draft) return [];
    return draft.workflow.connections
      .filter((item) => item.from.scope === "node_output")
      .map((item) => ({
        id: connectionId(item),
        source: String(item.from.nodeId),
        sourceHandle: `out:${item.from.port}`,
        target: item.to.nodeId,
        targetHandle: `in:${item.to.port}`,
        markerEnd: { type: MarkerType.ArrowClosed },
        animated: true,
      }));
  }, [draft]);

  const mutateWorkflow = useCallback((mutator: (workflow: WorkflowDefinition) => void) => {
    setValidationReport(null);
    setLiveRun(null);
    setAcknowledge(false);
    setDirty(true);
    moveToStep("review", draft?.draft_id);
    setDraft((current) => {
      if (!current) return current;
      const workflow = structuredClone(current.workflow);
      mutator(workflow);
      return { ...current, workflow, status: "draft" };
    });
  }, [draft?.draft_id, moveToStep]);

  const addAgent = (agent: AgentDefinition) => {
    if (!agent.execution?.outputSchema) return;
    mutateWorkflow((workflow) => {
      let suffix = 1;
      let nodeId = agent.slug.replace(/-/g, "_");
      while (workflow.nodes.some((node) => node.id === nodeId)) nodeId = `${agent.slug.replace(/-/g, "_")}_${++suffix}`;
      workflow.nodes.push({ id: nodeId, agentId: agent.slug, position: { x: 100 + workflow.nodes.length * 360, y: 120 } });
      for (const [port, schema] of Object.entries(agent.execution!.inputSchema.properties)) {
        if (schema["x-sapba-workflow-only"] === true) continue;
        const inputName = uniqueInputName(workflow.inputSchema, port, nodeId);
        workflow.inputSchema.properties[inputName] = structuredClone(schema);
        if ((agent.execution!.inputSchema.required ?? []).includes(port)) {
          workflow.inputSchema.required = [...(workflow.inputSchema.required ?? []), inputName];
        }
        workflow.connections.push({
          from: { scope: "workflow_input", port: inputName },
          to: { nodeId, port },
          transform: { type: "identity" },
        });
      }
      for (const port of ["business_status", "business_report"]) {
        const schema = agent.execution!.outputSchema!.properties[port];
        if (!schema) continue;
        const name = `${nodeId}_${port}`;
        workflow.outputSchema.properties[name] = structuredClone(schema);
        workflow.outputSchema.required = [...(workflow.outputSchema.required ?? []), name];
        workflow.outputs.push({ name, source: { scope: "node_output", nodeId, port }, transform: { type: "identity" } });
      }
      setSelectedNode(nodeId);
    });
  };

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return;
    const sourcePort = connection.sourceHandle.replace(/^out:/, "");
    const targetPort = connection.targetHandle.replace(/^in:/, "");
    mutateWorkflow((workflow) => {
      removeTargetMapping(workflow, connection.target!, targetPort);
      workflow.connections.push({
        from: { scope: "node_output", nodeId: connection.source!, port: sourcePort },
        to: { nodeId: connection.target!, port: targetPort },
        transform: { type: "identity" },
      });
    });
  }, [mutateWorkflow]);

  const compose = async () => {
    if (!requirement.trim()) return;
    setBusy(true); setMessage(""); setAdvanced(false); setValidationInputs({});
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/compose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirement: requirement.trim(), locale }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Composition failed");
      applyDraft(payload as Draft);
      moveToStep("compose", payload.draft_id);
      pollDraft(payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const continueComposition = async () => {
    if (!draft || !clarificationInput.trim()) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/composition-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: clarificationInput.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Composition failed");
      setClarificationInput(""); applyDraft(payload as Draft); pollDraft(payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const retryComposition = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/reconcile`, {
        method: "POST",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Composition retry failed");
      applyDraft(payload as Draft);
      if (payload.status === "planning") pollDraft(payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const createManualDraft = async () => {
    setBusy(true); setMessage(""); setAdvanced(true); setValidationInputs({});
    try {
      const workflow = emptyWorkflow();
      const response = await fetch(`${apiBase}/api/authoring/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: workflow.title, description: workflow.description, workflow }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Draft creation failed");
      applyDraft(payload as Draft);
      moveToStep("review", payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedRevision: draft.revision, workflow: draft.workflow }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Save failed");
      applyDraft(payload as Draft); setMessage(`${t.save} · r${payload.revision}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const sendWorkflowFeedback = async (fromValidation: boolean) => {
    if (!draft || !conversation || !feedbackText.trim()) return;
    const categoryValues = fromValidation ? t.feedbackCategoryValuesValidate : t.feedbackCategoryValuesReview;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          baseTurn: conversation.current_turn,
          baseRevision: draft.revision,
          feedback: feedbackText.trim(),
          feedbackTypeHint: feedbackType || categoryValues[0],
          locale,
          validationRunId: fromValidation ? validationReport?.run_id ?? null : null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Workflow feedback failed");
      setFeedbackText("");
      applyDraft(payload as Draft);
      await refreshConversation(payload.draft_id);
      pollDraft(payload.draft_id);
      if (fromValidation) moveToStep("validate", payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const continueWorkflowFeedback = async () => {
    if (!draft || !conversation || !feedbackInput.trim()) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/feedback-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseTurn: conversation.current_turn, input: feedbackInput.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Workflow feedback failed");
      setFeedbackInput(""); applyDraft(payload as Draft); await refreshConversation(payload.draft_id); pollDraft(payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const acceptDesign = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      let current = draft;
      if (dirty) {
        const saved = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(current.draft_id)}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expectedRevision: current.revision, workflow: current.workflow }),
        });
        const savedPayload = await saved.json();
        if (!saved.ok) throw new Error(savedPayload.detail?.message ?? "Save failed");
        current = savedPayload as Draft; applyDraft(current);
      }
      const conversationResponse = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(current.draft_id)}/conversation`);
      const currentConversation = (await conversationResponse.json()) as WorkflowConversation;
      if (!conversationResponse.ok) throw new Error("Workflow conversation unavailable");
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(current.draft_id)}/accept-design`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseTurn: currentConversation.current_turn, revision: current.revision, workflowHash: currentConversation.current_workflow_hash }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Design confirmation failed");
      applyDraft(payload as Draft); await refreshConversation(payload.draft_id); moveToStep("validate", payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const acceptValidation = async () => {
    if (!draft || !validationReport?.report_digest) return;
    if (validationReport.verdict === "inconclusive" && !acknowledge) return;
    setBusy(true); setMessage("");
    try {
      const gapCodes = (validationReport.evidence_gaps ?? []).map((item) => String(item.code ?? "")).filter(Boolean);
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/accept-validation`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ validationRunId: validationReport.run_id, validationReportDigest: validationReport.report_digest, acceptedGapCodes: gapCodes }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Validation confirmation failed");
      applyDraft(payload as Draft); await refreshConversation(payload.draft_id); moveToStep("publish", payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const undoRevision = async (targetRevision: number) => {
    if (!draft || !conversation) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/undo`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseTurn: conversation.current_turn, baseRevision: draft.revision, targetRevision }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Undo failed");
      applyDraft(payload as Draft); await refreshConversation(payload.draft_id); moveToStep("review", payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const startAnotherWorkflow = () => {
    setDraft(null); setConversation(null); setValidationReport(null); setLiveRun(null);
    setRequirement(""); setFeedbackText(""); setMessage(""); moveToStep("compose");
  };

  const validate = async () => {
    if (!draft) return;
    const requiredInputs = draft.workflow.inputSchema.required ?? [];
    const needsDiscovery = requiredInputs.some((name) => !(validationInputs[name] ?? "").trim());
    setBusy(true); setValidating(true); setMessage("");
    setValidationFeedback({ kind: "progress", text: needsDiscovery ? t.discovering : t.validating });
    try {
      let current = draft;
      if (current.status === "draft") {
        const saved = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(current.draft_id)}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expectedRevision: current.revision, workflow: current.workflow }),
        });
        const savedPayload = await saved.json();
        if (!saved.ok) throw new Error(savedPayload.detail?.message ?? "Save failed");
        current = savedPayload as Draft; applyDraft(current);
      }
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(current.draft_id)}/validate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          autoDiscover: true,
          expectations: expectations.map((item) => serializeExpectation(
            item,
            current.workflow.outputSchema.properties[item.output],
          )),
          input: Object.fromEntries(Object.entries(validationInputs)
            .filter(([, value]) => value.trim() !== "")
            .map(([name, value]) => [name, coerceValidationInput(value, current.workflow.inputSchema.properties[name]?.type)])),
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        const envelope = payload.detail && typeof payload.detail === "object" ? payload.detail : {};
        const errorDetail = envelope.detail && typeof envelope.detail === "object" ? envelope.detail : {};
        const missingFields: string[] = Array.isArray(errorDetail.missing_fields) ? errorDetail.missing_fields.map((item: unknown) => String(item)) : [];
        if (envelope.code === "workflow_validation_input_unavailable" && missingFields.length) {
          const localizedFields = missingFields.map((name) => current.workflow.inputSchema.properties[name]?.title?.[locale] ?? name);
          throw new Error(`${t.autoDiscoverFailed}: ${localizedFields.join(locale === "zh" ? "、" : ", ")}`);
        }
        const latestResponse = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(current.draft_id)}`);
        if (latestResponse.ok) applyDraft((await latestResponse.json()) as Draft);
        if (latestResponse.ok && ["workflow_runtime_review_blocked", "workflow_runtime_review_unavailable"].includes(String(envelope.code ?? ""))) {
          setValidationFeedback(null);
          return;
        }
        throw new Error(envelope.message ?? payload.detail ?? "Validation failed");
      }
      applyDraft(payload as Draft);
      setValidationFeedback(null);
      setAcknowledge(false);
      moveToStep("validate", payload.draft_id);
      if (payload.validation_run_id) connectValidationEvents(payload.draft_id, payload.validation_run_id);
      pollDraft(payload.draft_id);
    } catch (error) {
      setValidationFeedback({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally { setBusy(false); setValidating(false); }
  };

  const publish = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/publish`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          acknowledgeInconclusive: validationVerdict === "inconclusive" && validationAccepted,
          validationRunId: validationReport?.run_id ?? null,
          validationReportDigest: validationReport?.report_digest ?? null,
          acceptedGapCodes: validationVerdict === "inconclusive" && validationAccepted ? (validationReport?.evidence_gaps ?? []).map((item) => String(item.code ?? "")).filter(Boolean) : [],
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Publish failed");
      applyDraft(payload as Draft);
      setMessage(`${t.publish} · ${payload.validation?.branch ?? ""}`);
      onPublished?.(String(payload.workflow?.id ?? draft.workflow.id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const selected = draft?.workflow.nodes.find((node) => node.id === selectedNode);
  const selectedAgent = selected ? agentMap.get(selected.agentId) : undefined;
  const composition = draft?.composition;
  const gaps = composition?.gaps ?? [];
  const dismissedRequestedOutputs = composition?.output_normalization?.dismissed_requested_outputs ?? [];
  const compositionError = composition?.error;
  const legacyErrorMatch = compositionError?.message?.match(
    /Node\s+([^\s]+).*output\s+([A-Za-z0-9_]+)/,
  );
  const compositionErrorNode = compositionError?.detail?.node_id ?? legacyErrorMatch?.[1] ?? "—";
  const compositionErrorPort = compositionError?.detail?.port ?? legacyErrorMatch?.[2] ?? "—";
  const compositionErrorText = compositionError?.code === "workflow_conditional_skip_output_unavailable"
    ? t.unsafeSkipOutput
      .replace("{node}", compositionErrorNode)
      .replace("{port}", compositionErrorPort)
    : String(compositionError?.message ?? "");
  const compositionRetryable = compositionError?.code === "workflow_conditional_skip_output_unavailable"
    && Number(composition?.compiler_version ?? 0) < 4;
  const hasComposition = Boolean(composition?.requirement);
  const acceptedDesign = conversation?.accepted_design && typeof conversation.accepted_design === "object"
    ? conversation.accepted_design as Record<string, unknown>
    : null;
  const designAccepted = Boolean(draft && acceptedDesign
    && Number(acceptedDesign.revision ?? 0) === draft.revision
    && String(acceptedDesign.workflow_hash ?? "") === conversation?.current_workflow_hash);
  const designReady = Boolean(draft?.workflow.nodes.length) && gaps.length === 0 && draft?.status !== "planning" && draft?.status !== "waiting_input";
  const canValidate = designReady && designAccepted;
  const validationPhase = String(draft?.validation?.phase ?? (draft?.validation?.live_status === "running" ? "running" : "not_started"));
  const validationVerdict = String(validationReport?.verdict ?? draft?.validation?.verdict ?? "pending");
  const preflightReviewValue = draft?.validation?.preflight_review ?? draft?.validation?.runtime_review;
  const preflightReview = preflightReviewValue && typeof preflightReviewValue === "object"
    ? preflightReviewValue as Record<string, unknown>
    : null;
  const preflightIssues = Array.isArray(preflightReview?.issues)
    ? preflightReview.issues.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const preflightSummary = preflightReview?.summary && typeof preflightReview.summary === "object"
    ? localizedText(preflightReview.summary as { zh?: string; en?: string }, locale)
    : "";
  const reviewPolicyVersion = Number(
    draft?.validation?.review_policy_version
      ?? preflightReview?.review_policy_version
      ?? 0,
  );
  const preflightStale = validationVerdict === "blocked" && reviewPolicyVersion < WORKFLOW_REVIEW_POLICY_VERSION;
  const validationTerminal = validationReport?.phase === "completed" && ["pass", "inconclusive", "fail", "blocked"].includes(validationVerdict);
  const acceptedValidation = conversation?.accepted_validation && typeof conversation.accepted_validation === "object"
    ? conversation.accepted_validation as Record<string, unknown>
    : null;
  const validationAccepted = Boolean(validationReport?.report_digest && acceptedValidation
    && acceptedValidation.validation_run_id === validationReport.run_id
    && acceptedValidation.validation_report_digest === validationReport.report_digest);
  const validationAcceptable = validationTerminal && ["pass", "inconclusive"].includes(validationVerdict);
  const publishable = validationAcceptable && validationAccepted;
  const outputNames = Object.keys(draft?.workflow.outputSchema.properties ?? {});
  const progress = liveRun?.progress ?? validationReport?.progress ?? draft?.validation?.progress as Record<string, unknown> | undefined;
  const statusText = preflightStale
    ? t.preflightStale
    : validationStatusText(validationPhase, validationVerdict, draft?.validation_run_id, t);
  const addExpectation = () => {
    const output = outputNames.find((name) => !expectations.some((item) => item.output === name));
    if (!output) return;
    setExpectations((current) => [...current, { output, operator: "equals", expected: "" }]);
    setAcknowledge(false);
  };
  const updateExpectation = (index: number, value: ValidationExpectation) => {
    setExpectations((current) => current.map((item, itemIndex) => itemIndex === index ? value : item));
    setAcknowledge(false);
  };
  const pendingFeedback = composition?.conversation?.pending_feedback;

  return (
    <main className="workflow-builder-shell">
      <header className="workflow-builder-heading">
        <div><p className="eyebrow">Workflow Factory</p><h1>{t.title}</h1><p>{t.lead}</p></div>
      </header>

      <nav className="workflow-wizard" aria-label={locale === "zh" ? "工作流构建步骤" : "Workflow authoring steps"}>
        <ol>{(["compose", "review", "validate", "publish"] as WizardStep[]).map((step, index) => {
          const enabled = step === "compose" || step === "review" && Boolean(draft) || step === "validate" && canValidate || step === "publish" && publishable;
          return <li className={activeStep === step ? "is-active" : enabled ? "is-available" : ""} key={step}>
            <button disabled={!enabled} aria-current={activeStep === step ? "step" : undefined} onClick={() => moveToStep(step, draft?.draft_id)}><span>{index + 1}</span><strong>{t.steps[index]}</strong></button>
          </li>;
        })}</ol>
      </nav>

      {validationFeedback && <section className={`workflow-validation-feedback is-${validationFeedback.kind}`} role={validationFeedback.kind === "error" ? "alert" : "status"} aria-live="polite">{validationFeedback.kind === "progress" && <span className="workflow-spinner" />}{validationFeedback.text}</section>}
      {message && <p className="workflow-builder-message" role="status">{message}</p>}
      {draft && conversation && activeStep !== "publish" && <WorkflowConversationTimeline conversation={conversation} locale={locale} labels={t} onUndo={undoRevision} busy={busy} />}
      {draft?.status === "planning" && pendingFeedback && <section className="workflow-composition-state" aria-live="polite"><span className="workflow-spinner" />{t.feedbackPlanning}</section>}
      {draft?.status === "waiting_input" && pendingFeedback && <section className="workflow-clarification-card"><p className="eyebrow">{t.feedbackClarification}</p><h2>{composition?.clarification_question}</h2><div><input value={feedbackInput} placeholder={t.feedbackInput} onChange={(event) => setFeedbackInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void continueWorkflowFeedback(); }} /><button disabled={busy || !feedbackInput.trim()} onClick={continueWorkflowFeedback}>{t.continueCompose}</button></div></section>}

      {activeStep === "compose" && <section className="workflow-wizard-stage">
        <section className="workflow-intent-panel">
          <label htmlFor="workflow-requirement"><strong>{t.requirement}</strong></label>
          <textarea id="workflow-requirement" rows={4} value={requirement} placeholder={t.requirementPlaceholder} onChange={(event) => setRequirement(event.target.value)} />
          <div className="workflow-intent-actions"><small>{t.catalogOnly}</small><div><button disabled={busy} onClick={createManualDraft}>{t.manual}</button><button disabled={busy || !requirement.trim()} onClick={compose}>{t.compose}</button></div></div>
        </section>
        {draft?.status === "planning" && <section className="workflow-composition-state" aria-live="polite"><span className="workflow-spinner" />{t.composing}</section>}
        {compositionError?.message && <section className="workflow-composition-state is-error workflow-composition-error" role="alert"><strong>{t.compositionFailed}</strong><p>{compositionErrorText}</p><dl><div><dt>{t.issueNode}</dt><dd><code>{compositionErrorNode}</code></dd></div><div><dt>{t.issuePort}</dt><dd><code>{compositionErrorPort}</code></dd></div></dl>{compositionRetryable && <button disabled={busy} onClick={retryComposition}>{busy ? t.retryingComposition : t.retryComposition}</button>}</section>}
        {draft && !["planning", "waiting_input"].includes(draft.status) && !composition?.error?.message && <section className={`workflow-draft-ready${gaps.length ? " is-warning" : ""}`} role="status" aria-live="polite"><strong>{gaps.length ? `${t.draftReadyWithGaps} · ${gaps.length}` : t.draftReady}</strong><p>{draft.workflow.nodes.length} {t.draftStepUnit}{locale === "zh" ? "。" : "."} {t.draftReadyDetail}</p></section>}
        {dismissedRequestedOutputs.length > 0 && !compositionError?.message && <section className="workflow-normalization-note" role="note"><strong>{t.normalizedOutputs} · {dismissedRequestedOutputs.length}</strong><p>{t.normalizedOutputsHelp}</p><code>{dismissedRequestedOutputs.map((item) => `${item.stage_id}.${item.port}`).join(" · ")}</code></section>}
        {draft?.status === "waiting_input" && !pendingFeedback && <section className="workflow-clarification-card"><p className="eyebrow">{t.clarify}</p><h2>{composition?.clarification_question}</h2><div><input value={clarificationInput} placeholder={t.clarifyPlaceholder} onChange={(event) => setClarificationInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void continueComposition(); }} /><button disabled={busy || !clarificationInput.trim()} onClick={continueComposition}>{t.continueCompose}</button></div></section>}
        {draft && !["planning", "waiting_input"].includes(draft.status) && !compositionError?.message && <div className="workflow-stage-actions"><button className="primary" onClick={() => moveToStep("review", draft.draft_id)}>{t.nextReview}</button></div>}
      </section>}

      {activeStep === "review" && draft && <section className="workflow-wizard-stage">
        {dismissedRequestedOutputs.length > 0 && <section className="workflow-normalization-note" role="note"><strong>{t.normalizedOutputs} · {dismissedRequestedOutputs.length}</strong><p>{t.normalizedOutputsHelp}</p><code>{dismissedRequestedOutputs.map((item) => `${item.stage_id}.${item.port}`).join(" · ")}</code></section>}
        {hasComposition && <section className="workflow-composition-summary"><div className="workflow-intent-summary"><span>{t.intent}</span><h2>{localizedText(composition?.intent, locale) || requirement}</h2></div><div className="workflow-route-heading"><h2>{t.route}</h2><small>{draft.workflow.nodes.length} / {(composition?.stages ?? []).length} {t.matchedAgent}</small></div><ol className="workflow-route-list">{(composition?.stages ?? []).map((stage, index) => { const gap = gaps.find((item) => item.stage_id === stage.id); const agent = stage.agent_id ? agentMap.get(stage.agent_id) : undefined; return <li className={gap ? "is-gap" : "is-matched"} key={stage.id}><span className="workflow-route-index">{index + 1}</span><div><strong>{localizedText(stage.capability, locale)}</strong><small>{gap ? t.missingAgent : `${t.matchedAgent} · ${agent?.title[locale] ?? stage.agent_id}`}</small></div></li>; })}</ol></section>}
        {gaps.length > 0 && <section className="workflow-gap-panel"><header><div><p className="eyebrow">Blocked</p><h2>{t.gapTitle}</h2><p>{t.gapHelp}</p></div><strong>{gaps.length}</strong></header><div className="workflow-gap-list">{gaps.map((gap) => <article key={gap.gap_id}><div><h3>{localizedText(gap.title, locale)}</h3><p>{localizedText(gap.description, locale)}</p></div><dl><div><dt>Inputs</dt><dd>{gap.required_inputs.map((port) => `${port.name}: ${port.type}`).join(" · ") || "—"}</dd></div><div><dt>Outputs</dt><dd>{gap.required_outputs.map((port) => `${port.name}: ${port.type}`).join(" · ") || "—"}</dd></div></dl>{gap.agent_draft_id && <p className="workflow-gap-draft"><strong>{t.gapDraft}: {gap.agent_draft_id}</strong><br /><span>{t.awaitingCatalog}</span></p>}<a className="workflow-gap-action" href={`${askPath}?workflowDraft=${encodeURIComponent(draft.draft_id)}&gap=${encodeURIComponent(gap.gap_id)}`}>{t.createGapAgent}</a></article>)}</div></section>}
        <div className="workflow-review-toolbar"><button onClick={() => setAdvanced((value) => !value)}>{advanced ? t.hideAdvanced : t.advanced}</button>{advanced && <button disabled={busy} onClick={save}>{t.save}</button>}</div>
        {advanced && <section className="workflow-builder-grid"><aside className="workflow-agent-palette"><h2>{t.agents}</h2>{agents.map((agent) => <button key={agent.slug} onClick={() => addAgent(agent)}><strong>{agent.title[locale]}</strong><small>{agent.module} · {agent.slug}</small></button>)}</aside><div className="workflow-canvas" aria-label={t.title}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onConnect={onConnect} onNodeClick={(_, node) => setSelectedNode(node.id)} onNodesChange={(changes) => mutateWorkflow((workflow) => { for (const change of changes) if (change.type === "position" && change.position) { const item = workflow.nodes.find((node) => node.id === change.id); if (item) item.position = change.position; } })} fitView><Background /><MiniMap /><Controls /></ReactFlow></div><aside className="workflow-inspector"><h2>{t.metadata}</h2><div className="workflow-metadata-fields"><label><span>{t.workflowId}</span><input value={draft.workflow.id} pattern="[a-z][a-z0-9-]*" onChange={(event) => mutateWorkflow((workflow) => { workflow.id = event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"); })} /></label><label><span>{t.workflowName}</span><input value={draft.workflow.title[locale]} onChange={(event) => mutateWorkflow((workflow) => { workflow.title[locale] = event.target.value; })} /></label></div><h2>{t.mapping}</h2>{selected && selectedAgent ? <NodeInspector workflow={draft.workflow} nodeId={selected.id} agent={selectedAgent} agents={agentMap} locale={locale} onChange={mutateWorkflow} onRemove={() => { mutateWorkflow((workflow) => removeNode(workflow, selected.id)); setSelectedNode(null); }} removeLabel={t.remove} /> : <p>{t.selectNode}</p>}</aside></section>}
        <WorkflowFeedbackComposer labels={t} validation={false} value={feedbackText} typeValue={feedbackType} onValue={setFeedbackText} onType={setFeedbackType} onSubmit={() => void sendWorkflowFeedback(false)} onNewWorkflow={startAnotherWorkflow} busy={busy} />
        {designAccepted && <p className="workflow-acceptance-note">{t.designAccepted}</p>}
        <div className="workflow-stage-actions"><button onClick={() => moveToStep("compose", draft.draft_id)}>{t.back}</button><button className="primary" disabled={!designReady || busy} onClick={acceptDesign}>{t.acceptDesign}</button></div>
      </section>}

      {activeStep === "validate" && draft && <section className="workflow-wizard-stage">
        <section className="workflow-validation-panel">
          <div className={`workflow-validation-status is-${validationVerdict}`} aria-live="polite">{["preflight", "queued", "running"].includes(validationPhase) && <span className="workflow-spinner" />}<div><strong>{statusText}</strong>{draft.validation_run_id && <small>{draft.validation_run_id}</small>}{["preflight", "queued", "running"].includes(validationPhase) && Boolean(progress?.phase) && <small>{String(progress?.phase)}{progress?.current_node_id ? ` · ${String(progress.current_node_id)}` : ""}</small>}</div></div>
          {validationVerdict === "blocked" && preflightReview && <section className="workflow-preflight-blocked" role="alert"><header><div><p className="eyebrow">{t.preflightTitle}</p><h2>{preflightStale ? t.preflightStale : t.preflightBlockedTitle}</h2></div><strong>{preflightIssues.length}</strong></header><p>{preflightSummary || t.preflightBlockedHelp}</p><p className="workflow-preflight-no-sap">{preflightStale ? t.preflightStaleHelp : t.preflightNoSap}</p>{preflightIssues.length > 0 && <div className="workflow-preflight-issues">{preflightIssues.map((issue, index) => { const issueMessage = issue.message && typeof issue.message === "object" ? localizedText(issue.message as { zh?: string; en?: string }, locale) : String(issue.message ?? ""); return <article key={`${String(issue.code ?? "issue")}:${index}`}><div><code>{String(issue.code ?? "workflow_review_issue")}</code><strong>{issueMessage}</strong></div><dl>{issue.node_id != null && <div><dt>{t.issueNode}</dt><dd><code>{String(issue.node_id)}</code></dd></div>}{issue.port != null && <div><dt>{t.issuePort}</dt><dd><code>{String(issue.port)}</code></dd></div>}</dl></article>; })}</div>}</section>}
          <h2>{t.validationInputs}</h2><p>{t.autoDiscover}</p>
          <div className="workflow-validation-inputs">{Object.entries(draft.workflow.inputSchema.properties ?? {}).filter(([, schema]) => schema["x-sapba-workflow-only"] !== true).map(([name, schema]) => { const required = (draft.workflow.inputSchema.required ?? []).includes(name); return <label className={required ? "is-required" : ""} key={name}><span>{schema.title?.[locale] ?? name}{required && <em>{t.required}</em>}</span>{schema.type === "array" ? <textarea rows={4} aria-required={required} value={validationInputs[name] ?? ""} placeholder={schema.placeholder?.[locale] ?? (locale === "zh" ? "每行或用逗号分隔" : "One per line or comma-separated")} onChange={(event) => setValidationInputs((current) => ({ ...current, [name]: event.target.value }))} /> : <input type={schema.format === "date" ? "date" : schema.type === "number" || schema.type === "integer" ? "number" : "text"} aria-required={required} value={validationInputs[name] ?? ""} placeholder={schema.placeholder?.[locale] ?? name} onChange={(event) => setValidationInputs((current) => ({ ...current, [name]: event.target.value }))} />}</label>; })}</div>
          <div className="workflow-expectations"><div><h2>{t.expectations}</h2><p>{t.expectationsHelp}</p></div>{expectations.map((item, index) => <div className="workflow-expectation-row" key={`${item.output}:${index}`}><label><span>{t.expectedOutput}</span><select value={item.output} onChange={(event) => updateExpectation(index, { ...item, output: event.target.value })}>{outputNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label><span>{t.expectedOperator}</span><select value={item.operator} onChange={(event) => { const operator = event.target.value as ValidationExpectation["operator"]; updateExpectation(index, { output: item.output, operator, ...(operator === "exists" || operator === "non_empty" ? {} : { expected: "" }), ...(operator === "decimal_within" ? { tolerance: "0.01" } : {}) }); }}>{["equals", "one_of", "exists", "non_empty", "decimal_within"].map((operator) => <option key={operator}>{operator}</option>)}</select></label>{!["exists", "non_empty"].includes(item.operator) && <label><span>{t.expectedValue}</span><input value={Array.isArray(item.expected) ? item.expected.join(", ") : String(item.expected ?? "")} onChange={(event) => updateExpectation(index, { ...item, expected: item.operator === "one_of" ? event.target.value.split(/[,，]/).map((value) => value.trim()).filter(Boolean) : event.target.value })} /></label>}{item.operator === "decimal_within" && <label><span>{t.tolerance}</span><input type="number" min="0" step="any" value={item.tolerance ?? "0.01"} onChange={(event) => updateExpectation(index, { ...item, tolerance: event.target.value })} /></label>}<button className="danger-button" onClick={() => setExpectations((current) => current.filter((_, itemIndex) => itemIndex !== index))}>{t.remove}</button></div>)}<button disabled={expectations.length >= Math.min(20, outputNames.length)} onClick={addExpectation}>{t.addExpectation}</button></div>
          <div className="workflow-stage-actions"><button onClick={() => moveToStep("review", draft.draft_id)}>{validationVerdict === "blocked" ? t.returnToReview : t.back}</button><button className="primary" disabled={busy || !canValidate} onClick={validate}>{validating && <span className="workflow-spinner workflow-spinner--button" />}{validating ? t.validating : validationVerdict === "blocked" ? t.retryPreflight : t.validate}</button></div>
        </section>
        {validationReport?.phase === "completed" && <ValidationReportPanel report={validationReport} locale={locale} apiBase={apiBase} draftId={draft.draft_id} runPath={runPath} labels={t} />}
        {validationReport?.phase === "completed" && <WorkflowFeedbackComposer labels={t} validation value={feedbackText} typeValue={feedbackType} onValue={setFeedbackText} onType={setFeedbackType} onSubmit={() => void sendWorkflowFeedback(true)} onNewWorkflow={startAnotherWorkflow} busy={busy} />}
        {validationReport?.phase === "completed" && validationReport.verdict === "inconclusive" && <div className="workflow-acknowledgement"><EvidenceGapList gaps={validationReport.evidence_gaps ?? []} locale={locale} emptyLabel={t.noEvidenceGaps} /><label><input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} /><span>{t.acknowledgeDetailed}</span></label><small>{t.acknowledgementHelp}</small></div>}
        {validationAccepted && <p className="workflow-acceptance-note">{t.validationAccepted}</p>}
        {validationReport?.phase === "completed" && <div className="workflow-stage-actions"><button className="primary" disabled={!validationAcceptable || busy || validationVerdict === "inconclusive" && !acknowledge} onClick={acceptValidation}>{t.acceptValidation}</button></div>}
      </section>}

      {activeStep === "publish" && draft && <section className="workflow-wizard-stage"><section className="workflow-publish-panel"><h2>{t.publishSummary}</h2>{validationReport ? <><dl><div><dt>{t.statusLabel}</dt><dd><strong>{validationStatusText("completed", validationReport.verdict, validationReport.run_id, t)}</strong></dd></div><div><dt>Run</dt><dd><code>{validationReport.run_id}</code></dd></div><div><dt>{t.reportDigest}</dt><dd><code>{validationReport.report_digest ?? "—"}</code></dd></div></dl><p>{publishable ? t.publishReady : t.publishBlocked}</p>{validationReport.verdict === "inconclusive" && <div className="workflow-acknowledgement"><h3>{t.evidenceGaps}</h3><EvidenceGapList gaps={validationReport.evidence_gaps ?? []} locale={locale} emptyLabel={t.noEvidenceGaps} /><p>{t.validationAccepted}</p><small>{t.acknowledgementHelp}</small></div>}</> : <p>{t.noValidation}</p>}<div className="workflow-stage-actions"><button onClick={() => moveToStep("validate", draft.draft_id)}>{t.back}</button><button className="primary" disabled={busy || !publishable} onClick={publish}>{t.publish}</button></div></section></section>}
    </main>
  );
}

function WorkflowConversationTimeline({ conversation, locale, labels: viewLabels, onUndo, busy }: { conversation: WorkflowConversation; locale: Locale; labels: typeof labels.zh | typeof labels.en; onUndo: (revision: number) => void; busy: boolean }) {
  return <section className="workflow-conversation" aria-live="polite">
    <header><div><p className="eyebrow">Conversation</p><h2>{viewLabels.conversationTitle}</h2><p>{viewLabels.conversationHelp}</p></div><strong>{conversation.current_turn}/{conversation.turn_limit}</strong></header>
    <div className="workflow-conversation-turns">{conversation.turns.map((turn) => {
      const latest = turn.turn === conversation.current_turn;
      const summary = turn.decision?.summary && typeof turn.decision.summary === "object"
        ? localizedText(turn.decision.summary as { zh?: string; en?: string }, locale)
        : String(turn.decision?.reason ?? turn.decision?.clarification_question ?? "");
      const revisionChanged = Number(turn.result_revision ?? 0) !== Number(turn.base_revision ?? 0) && (turn.diff?.length ?? 0) > 0;
      const mayUndo = latest && revisionChanged && Number(turn.base_revision ?? 0) > 0 && turn.status === "completed";
      return <details className={`workflow-conversation-turn is-${turn.status}`} open={latest} key={turn.turn}>
        <summary><span>{turn.turn}</span><strong>{viewLabels.turnKinds[turn.kind as keyof typeof viewLabels.turnKinds] ?? turn.kind}</strong><em>{turn.status}</em></summary>
        <div className="workflow-conversation-turn-body">
          {turn.user_message && <blockquote>{turn.user_message}</blockquote>}
          {summary && <p>{summary}</p>}
          <dl><div><dt>Revision</dt><dd>{turn.base_revision ?? "—"} → {turn.result_revision ?? "—"}</dd></div><div><dt>{locale === "zh" ? "处理" : "Action"}</dt><dd>{turn.action ?? "—"}</dd></div><div><dt>SAP</dt><dd>{turn.validation_run_id ? `${viewLabels.readSapAgain} · ${turn.validation_run_id}` : viewLabels.noSapRead}</dd></div><div><dt>Diff</dt><dd>{revisionChanged ? `${viewLabels.changedRevision} · ${turn.diff?.length ?? 0}` : viewLabels.reusedDesign}</dd></div></dl>
          {mayUndo && <button disabled={busy} onClick={() => onUndo(Number(turn.base_revision))}>{viewLabels.undoTurn}</button>}
        </div>
      </details>;
    })}</div>
  </section>;
}

function WorkflowFeedbackComposer({ labels: viewLabels, validation, value, typeValue, onValue, onType, onSubmit, onNewWorkflow, busy }: { labels: typeof labels.zh | typeof labels.en; validation: boolean; value: string; typeValue: string; onValue: (value: string) => void; onType: (value: string) => void; onSubmit: () => void; onNewWorkflow: () => void; busy: boolean }) {
  const categoryLabels = validation ? viewLabels.feedbackCategoriesValidate : viewLabels.feedbackCategoriesReview;
  const categoryValues = validation ? viewLabels.feedbackCategoryValuesValidate : viewLabels.feedbackCategoryValuesReview;
  return <section className="workflow-feedback-composer">
    <header><h2>{viewLabels.feedbackTitle}</h2></header>
    <div className="workflow-feedback-categories">{categoryLabels.map((label, index) => <button className={(typeValue || categoryValues[0]) === categoryValues[index] ? "is-selected" : ""} key={categoryValues[index]} onClick={() => onType(categoryValues[index])}>{label}</button>)}</div>
    <textarea rows={4} value={value} placeholder={viewLabels.feedbackPlaceholder} onChange={(event) => onValue(event.target.value)} />
    <div className="workflow-feedback-actions"><button onClick={onNewWorkflow}>{viewLabels.newWorkflowIntent}</button><button className="primary" disabled={busy || !value.trim()} onClick={onSubmit}>{viewLabels.sendFeedback}</button></div>
  </section>;
}

function localizedText(value: { zh?: string; en?: string } | undefined, locale: Locale): string {
  return String(value?.[locale] || value?.zh || value?.en || "");
}

function validationStatusText(
  phase: string,
  verdict: string,
  _runId: string | null | undefined,
  viewLabels: typeof labels.zh | typeof labels.en,
): string {
  if (phase === "preflight") return viewLabels.preflightPassed;
  if (["queued", "running"].includes(phase)) return viewLabels.validationRunning;
  if (verdict === "pass") return viewLabels.validationPassed;
  if (verdict === "inconclusive") return viewLabels.validationInconclusive;
  if (verdict === "fail") return viewLabels.validationFailed;
  if (verdict === "blocked") return viewLabels.validationBlocked;
  return viewLabels.noValidation;
}

function ValidationReportPanel({ report, locale, apiBase, draftId, runPath, labels: viewLabels }: { report: ValidationReport; locale: Locale; apiBase: string; draftId: string; runPath: string; labels: typeof labels.zh | typeof labels.en }) {
  const checks = report.automatic_checks ?? [];
  const nodes = report.node_results ?? [];
  const outputChecks = report.required_output_checks ?? [];
  const expectations = report.user_expectations ?? [];
  const businessResult = report.business_result ?? {};
  const statusOutputs = businessResult.status_outputs && typeof businessResult.status_outputs === "object" ? businessResult.status_outputs as Record<string, unknown> : {};
  const completeness = report.completeness ?? {};
  const review = report.preflight_review ?? {};
  const reviewSummary = review.summary && typeof review.summary === "object" ? localizedText(review.summary as { zh?: string; en?: string }, locale) : "";
  return <section className="workflow-validation-report">
    <header><div><p className="eyebrow">{viewLabels.statusLabel}</p><h2>{viewLabels.reportTitle}</h2><p>{viewLabels.reportHelp}</p></div><span className={`workflow-verdict is-${report.verdict}`}>{validationStatusText("completed", report.verdict, report.run_id, viewLabels)}</span></header>
    <div className="workflow-report-overview">
      <article><span>{viewLabels.technicalResult}</span><strong>{report.verdict}</strong></article>
      <article><span>{viewLabels.businessResult}</span><strong>{Object.values(statusOutputs).map(String).join(" · ") || String(businessResult.run_status ?? "—")}</strong></article>
      <article><span>{viewLabels.completenessResult}</span><strong>{booleanLabel(completeness.source_complete, locale)} / {booleanLabel(completeness.business_complete, locale)}</strong></article>
      <article><span>{viewLabels.expectationResult}</span><strong>{expectations.length ? `${expectations.filter((item) => item.status === "pass").length}/${expectations.length}` : "—"}</strong></article>
    </div>
    <section className="workflow-report-section"><h3>{viewLabels.preflightTitle}</h3><p>{reviewSummary || "—"}</p></section>
    <section className="workflow-report-section"><h3>{viewLabels.normalizedInput}</h3><dl className="workflow-report-kv">{Object.entries(report.normalized_input ?? {}).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{displayValue(value)}</dd></div>)}</dl><p><strong>{viewLabels.sampleSource}:</strong> {report.sample_source === "auto_discovered" ? viewLabels.autoDiscovered : viewLabels.userProvided}</p></section>
    <section className="workflow-report-section"><h3>{viewLabels.automaticChecks}</h3><ul className="workflow-check-list">{checks.map((item) => <li className={`is-${String(item.status ?? "")}`} key={String(item.id)}><strong>{String(item.id)}</strong><span>{localizedText(item.summary as { zh?: string; en?: string }, locale)}</span><em>{String(item.status)}</em></li>)}</ul></section>
    <section className="workflow-report-section"><h3>{viewLabels.nodeResults}</h3><div className="workflow-report-table-wrap"><table><thead><tr><th>{viewLabels.columns.node}</th><th>{viewLabels.columns.agent}</th><th>{viewLabels.columns.state}</th><th>{viewLabels.columns.childRun}</th><th>{viewLabels.columns.duration}</th><th>{viewLabels.columns.business}</th><th>{viewLabels.columns.source}</th><th>{viewLabels.columns.evidence}</th><th>{viewLabels.columns.tools}</th></tr></thead><tbody>{nodes.map((item) => <tr key={String(item.node_id)}><td>{String(item.node_id)}</td><td>{String(item.agent_id)}</td><td>{String(item.status)}</td><td>{item.child_run_id ? <a href={`${runPath}?run=${encodeURIComponent(String(item.child_run_id))}`}>{String(item.child_run_id)}</a> : "—"}</td><td>{formatDuration(item.duration_ms)}</td><td>{String(item.business_status ?? "—")}</td><td>{booleanLabel(item.source_complete, locale)}</td><td>{booleanLabel(item.evidence_complete, locale)}</td><td>{String(item.tool_call_count ?? 0)}</td></tr>)}</tbody></table></div></section>
    <section className="workflow-report-section"><h3>{viewLabels.requiredOutputs}</h3><div className="workflow-report-table-wrap"><table><thead><tr><th>{viewLabels.expectedOutput}</th><th>{viewLabels.columns.state}</th><th>{viewLabels.columns.result}</th></tr></thead><tbody>{outputChecks.map((item) => <tr key={String(item.output)}><td>{String(item.output)}</td><td>{String(item.status)}</td><td>{String(item.value_summary ?? "—")}</td></tr>)}</tbody></table></div></section>
    <section className="workflow-report-section"><h3>{viewLabels.expectations}</h3>{expectations.length ? <div className="workflow-report-table-wrap"><table><thead><tr><th>{viewLabels.expectedOutput}</th><th>{viewLabels.expectedOperator}</th><th>{viewLabels.expectedValue}</th><th>{viewLabels.columns.result}</th></tr></thead><tbody>{expectations.map((item, index) => <tr key={`${String(item.output)}:${index}`}><td>{String(item.output)}</td><td>{String(item.operator)}</td><td>{displayValue(item.expected)}</td><td>{String(item.status)} · {displayValue(item.actual)}</td></tr>)}</tbody></table></div> : <p>{viewLabels.noExpectations}</p>}</section>
    <section className="workflow-report-section"><h3>{viewLabels.evidenceGaps}</h3><EvidenceGapList gaps={report.evidence_gaps ?? []} locale={locale} emptyLabel={viewLabels.noEvidenceGaps} /></section>
    <footer><a href={`${runPath}?run=${encodeURIComponent(report.run_id)}`}>{viewLabels.openRun}</a><a href={`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}/validation-artifacts/workflow-validation-report.json`}>{viewLabels.downloadJson}</a><a href={`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}/validation-artifacts/workflow-validation-report.md`}>{viewLabels.downloadMarkdown}</a><code>{report.report_digest}</code></footer>
  </section>;
}

function EvidenceGapList({ gaps, locale, emptyLabel }: { gaps: Array<Record<string, unknown>>; locale: Locale; emptyLabel: string }) {
  if (!gaps.length) return <p>{emptyLabel}</p>;
  return <div className="workflow-gap-evidence-list">{gaps.map((item) => <article key={String(item.code)}><code>{String(item.code)}</code><strong>{localizedText(item.missing as { zh?: string; en?: string }, locale)}</strong><p>{localizedText(item.impact as { zh?: string; en?: string }, locale)}</p><small>{localizedText(item.display_behavior as { zh?: string; en?: string }, locale)}</small></article>)}</div>;
}

function booleanLabel(value: unknown, locale: Locale): string {
  if (value === true) return locale === "zh" ? "完整" : "Complete";
  if (value === false) return locale === "zh" ? "不完整" : "Incomplete";
  return "—";
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => displayValue(item)).join(", ") || "[]";
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${displayValue(item)}`).join(" · ");
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function formatDuration(value: unknown): string {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds)) return "—";
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${milliseconds}ms`;
}

function serializeExpectation(expectation: ValidationExpectation, schema: ExecutionInputProperty | undefined): ValidationExpectation {
  if (["exists", "non_empty"].includes(expectation.operator)) return { output: expectation.output, operator: expectation.operator };
  if (expectation.operator === "decimal_within") return { ...expectation, expected: String(expectation.expected ?? ""), tolerance: String(expectation.tolerance ?? "0.01") };
  const convert = (value: unknown): unknown => {
    const type = Array.isArray(schema?.type) ? schema?.type.find((item) => item !== "null") : schema?.type;
    if (type === "boolean") return String(value).toLowerCase() === "true";
    if (type === "integer") return Number.parseInt(String(value), 10);
    if (type === "number") return Number(value);
    return value;
  };
  if (expectation.operator === "one_of") return { ...expectation, expected: (Array.isArray(expectation.expected) ? expectation.expected : [expectation.expected]).map(convert) };
  return { ...expectation, expected: convert(expectation.expected) };
}

function coerceValidationInput(value: string, type?: ExecutionInputProperty["type"]): unknown {
  const scalarType = Array.isArray(type) ? type.find((item) => item !== "null") : type;
  if (scalarType === "array") return Array.from(new Set(value.split(/[\r\n,;，；]+/).map((item) => item.trim()).filter(Boolean)));
  if (scalarType === "integer") return Number.parseInt(value, 10);
  if (scalarType === "number") return Number(value);
  if (scalarType === "boolean") return value.toLowerCase() === "true";
  return value;
}

function NodeInspector({ workflow, nodeId, agent, agents, locale, onChange, onRemove, removeLabel }: { workflow: WorkflowDefinition; nodeId: string; agent: AgentDefinition; agents: Map<string, AgentDefinition>; locale: Locale; onChange: (fn: (workflow: WorkflowDefinition) => void) => void; onRemove: () => void; removeLabel: string }) {
  const node = workflow.nodes.find((item) => item.id === nodeId)!;
  const mappings = Object.keys(agent.execution?.inputSchema.properties ?? {});
  const options = workflow.nodes.flatMap((candidate) => {
    if (candidate.id === nodeId) return [];
    const sourceAgent = agents.get(candidate.agentId);
    return Object.keys(sourceAgent?.execution?.outputSchema?.properties ?? {}).map((port) => ({ value: `${candidate.id}:${port}`, label: `${sourceAgent?.title[locale]} · ${port}` }));
  });
  const arraySources = [
    ...Object.entries(workflow.inputSchema.properties).filter(([, schema]) => schema.type === "array").map(([port]) => ({ value: `input:${port}`, label: `${locale === "zh" ? "工作流输入" : "Workflow input"} · ${port}` })),
    ...workflow.nodes.flatMap((candidate) => {
      if (candidate.id === nodeId) return [];
      const sourceAgent = agents.get(candidate.agentId);
      return Object.entries(sourceAgent?.execution?.outputSchema?.properties ?? {}).filter(([, schema]) => schema.type === "array").map(([port]) => ({ value: `node:${candidate.id}:${port}`, label: `${sourceAgent?.title[locale]} · ${port}` }));
    }),
  ];
  const foreachSource = node.forEach?.source.scope === "workflow_input" ? `input:${node.forEach.source.port}` : node.forEach?.source.scope === "node_output" ? `node:${node.forEach.source.nodeId}:${node.forEach.source.port}` : "";
  const groupByText = Object.entries(node.forEach?.groupBy ?? {}).map(([name, pointer]) => `${name}=${pointer}`).join(", ");
  const aggregateRules = workflow.outputs.filter((item) => item.aggregate?.sources.some((source) => source.nodeId === nodeId));

  return <div>
    <section className="workflow-loop-editor">
      <label><input type="checkbox" checked={Boolean(node.forEach)} onChange={(event) => onChange((next) => {
        const target = next.nodes.find((item) => item.id === nodeId)!;
        if (!event.target.checked) { delete target.forEach; return; }
        next.schemaVersion = 2;
        const selected = arraySources[0]?.value ?? "";
        target.forEach = {
          source: parseForeachSource(selected),
          maxItems: 50,
          maxConcurrency: 4,
          onItemError: "collect_inconclusive",
        };
      })} />{locale === "zh" ? "按集合逐项执行（foreach）" : "Execute once per collection item (foreach)"}</label>
    {node.forEach && <>
        <label><span>{locale === "zh" ? "循环来源" : "Loop source"}</span><select value={foreachSource} onChange={(event) => onChange((next) => { next.nodes.find((item) => item.id === nodeId)!.forEach!.source = parseForeachSource(event.target.value); })}>{arraySources.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <label><span>{locale === "zh" ? "分组键（名称=JSON Pointer）" : "Group keys (name=JSON Pointer)"}</span><input value={groupByText} placeholder="company_code=/company_code, supplier=/supplier" onChange={(event) => onChange((next) => {
          const spec = next.nodes.find((item) => item.id === nodeId)!.forEach!;
          const entries = event.target.value.split(/[,;\n]+/).map((item) => item.trim()).filter(Boolean).map((item) => item.split("=", 2).map((part) => part.trim())).filter((item) => item.length === 2 && item[0] && item[1]);
          if (entries.length) spec.groupBy = Object.fromEntries(entries); else delete spec.groupBy;
        })} /></label>
        <label><span>{locale === "zh" ? "最大项目数" : "Maximum items"}</span><input type="number" min={1} max={50} value={node.forEach.maxItems ?? 50} onChange={(event) => onChange((next) => { next.nodes.find((item) => item.id === nodeId)!.forEach!.maxItems = Number(event.target.value); })} /></label>
        <label><span>{locale === "zh" ? "最大并发" : "Maximum concurrency"}</span><input type="number" min={1} max={8} value={node.forEach.maxConcurrency ?? 4} onChange={(event) => onChange((next) => { next.nodes.find((item) => item.id === nodeId)!.forEach!.maxConcurrency = Number(event.target.value); })} /></label>
      </>}
    </section>
    {node.runIf && <section className="workflow-conditional-summary">
      <strong>{locale === "zh" ? "条件终态" : "Conditional terminal outcome"}</strong>
      <p>{locale === "zh" ? "上游集合为空时不创建子运行，并返回显式的不确定终态。" : "When the upstream collection is empty, no child run is created and an explicit inconclusive outcome is returned."}</p>
      <code>{node.onSkip?.reasonCode ?? (locale === "zh" ? "缺少 onSkip 输出" : "Missing onSkip output")}</code>
      {node.onSkip && <small>{Object.keys(node.onSkip.outputs).join(", ")}</small>}
    </section>}
    {mappings.map((port) => {
      const connection = workflow.connections.find((item) => item.to.nodeId === nodeId && item.to.port === port);
      const value = connection?.from.scope === "node_output" ? `${connection.from.nodeId}:${connection.from.port}` : connection?.from.scope === "workflow_input" ? `input:${connection.from.port}` : connection?.from.scope === "iteration_item" ? `iteration:${connection.from.pointer ?? "/"}` : "constant";
      return <label className="workflow-mapping-row" key={port}><span>{port}</span><select value={value} onChange={(event) => onChange((next) => {
        removeTargetMapping(next, nodeId, port, false);
        const selected = event.target.value;
        if (selected.startsWith("input:")) next.connections.push({ from: { scope: "workflow_input", port: selected.slice(6) }, to: { nodeId, port }, transform: { type: "identity" } });
        else if (selected.startsWith("iteration:")) next.connections.push({ from: { scope: "iteration_item", pointer: selected.slice(10) || "/" }, to: { nodeId, port }, transform: { type: "identity" } });
        else if (selected === "constant") next.connections.push({ from: { scope: "constant", value: "" }, to: { nodeId, port }, transform: { type: "identity" } });
        else { const [sourceNode, sourcePort] = selected.split(":"); next.connections.push({ from: { scope: "node_output", nodeId: sourceNode, port: sourcePort }, to: { nodeId, port }, transform: { type: "identity" } }); }
      })}>{Object.keys(workflow.inputSchema.properties).map((name) => <option key={name} value={`input:${name}`}>{name}</option>)}{node.forEach && <><option value="iteration:/">{locale === "zh" ? "当前迭代项" : "Current iteration item"}</option><option value="iteration:/items">{locale === "zh" ? "当前分组项目数组" : "Current grouped items"}</option></>}{value.startsWith("iteration:") && !["iteration:/", "iteration:/items"].includes(value) && <option value={value}>{value}</option>}{options.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}<option value="constant">Constant</option></select>
      {connection && <select aria-label={`${port} transform`} value={connection.transform?.type ?? "identity"} onChange={(event) => onChange((next) => { const item = next.connections.find((entry) => entry.to.nodeId === nodeId && entry.to.port === port); if (item) item.transform = { type: event.target.value }; })}><option value="identity">identity</option><option value="wrap_array">wrap_array</option></select>}
      {connection?.from.scope === "constant" && <input value={String(connection.from.value ?? "")} onChange={(event) => onChange((next) => { const item = next.connections.find((entry) => entry.to.nodeId === nodeId && entry.to.port === port); if (item) item.from.value = event.target.value; })} />}</label>;
    })}
    {aggregateRules.length > 0 && <section className="workflow-aggregate-summary"><strong>{locale === "zh" ? "聚合规则" : "Aggregation rules"}</strong><ul>{aggregateRules.map((item) => <li key={item.name}>{item.name}: {item.aggregate!.operator}{item.aggregate!.precedence?.length ? ` (${item.aggregate!.precedence.join(" > ")})` : ""}</li>)}</ul></section>}
    <button className="danger-button" onClick={onRemove}>{removeLabel}</button>
  </div>;
}

function parseForeachSource(value: string): { scope: "workflow_input" | "node_output"; port: string; nodeId?: string } {
  if (value.startsWith("input:")) return { scope: "workflow_input", port: value.slice(6) };
  const [, nodeId = "", port = ""] = value.split(":");
  return { scope: "node_output", nodeId, port };
}

function uniqueInputName(schema: ExecutionInputSchema, port: string, nodeId: string): string {
  if (!(port in schema.properties)) return port;
  let name = `${nodeId}_${port}`; let index = 1;
  while (name in schema.properties) name = `${nodeId}_${port}_${++index}`;
  return name;
}

function removeTargetMapping(workflow: WorkflowDefinition, nodeId: string, port: string, prune = true) {
  const old = workflow.connections.find((item) => item.to.nodeId === nodeId && item.to.port === port);
  workflow.connections = workflow.connections.filter((item) => !(item.to.nodeId === nodeId && item.to.port === port));
  if (prune && old?.from.scope === "workflow_input" && old.from.port && !workflow.connections.some((item) => item.from.scope === "workflow_input" && item.from.port === old.from.port)) {
    delete workflow.inputSchema.properties[old.from.port];
    workflow.inputSchema.required = (workflow.inputSchema.required ?? []).filter((name) => name !== old.from.port);
  }
}

function removeNode(workflow: WorkflowDefinition, nodeId: string) {
  workflow.nodes = workflow.nodes.filter((node) => node.id !== nodeId);
  const targets = workflow.connections.filter((item) => item.to.nodeId === nodeId);
  workflow.connections = workflow.connections.filter((item) => item.to.nodeId !== nodeId && !(item.from.scope === "node_output" && item.from.nodeId === nodeId));
  for (const item of targets) if (item.from.scope === "workflow_input" && item.from.port && !workflow.connections.some((entry) => entry.from.scope === "workflow_input" && entry.from.port === item.from.port)) { delete workflow.inputSchema.properties[item.from.port]; workflow.inputSchema.required = (workflow.inputSchema.required ?? []).filter((name) => name !== item.from.port); }
  workflow.outputs = workflow.outputs.filter((item) => !(item.source?.scope === "node_output" && item.source.nodeId === nodeId) && !item.aggregate?.sources.some((source) => source.scope === "node_output" && source.nodeId === nodeId));
  for (const name of Object.keys(workflow.outputSchema.properties).filter((name) => name.startsWith(`${nodeId}_`))) { delete workflow.outputSchema.properties[name]; workflow.outputSchema.required = (workflow.outputSchema.required ?? []).filter((item) => item !== name); }
}
