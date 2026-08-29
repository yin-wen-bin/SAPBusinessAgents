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

type BuilderProps = { apiBase: string; locale: Locale; runPath: string; askPath: string };
type AgentNodeData = { agent: AgentDefinition; locale: Locale };

const labels = {
  zh: {
    title: "用一句话生成工作流",
    lead: "描述业务目标，Codex 会从当前仓库的可执行 Agent 中自动选择、排序并连接输入输出。",
    requirement: "你希望完成什么业务任务？",
    requirementPlaceholder: "例如：检查指定采购订单从收货、发票到清账的完整状态，并输出缺失环节和下一步。",
    compose: "生成工作流草稿",
    composing: "正在匹配 Agent 并编译工作流…",
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
    clarify: "Codex 需要确认一个关键信息",
    clarifyPlaceholder: "请直接回答这个问题",
    continueCompose: "继续生成",
    reconciled: "已检查当前 Agent 目录",
    catalogOnly: "只会采用当前仓库中状态为可执行的 Agent，并固定版本与摘要。",
    agents: "可用 Agents",
    save: "保存草稿",
    validate: "Codex 真机验证",
    publish: "发布固定工作流",
    acknowledge: "我确认并接受本次验证中的完整性缺口",
    selectNode: "选择一个节点配置输入映射",
    mapping: "输入映射",
    metadata: "工作流信息",
    workflowId: "工作流标识",
    workflowName: "工作流名称",
    validationInputs: "真机验证输入",
    autoDiscover: "留空时自动发现可用的真机样本",
    noIssues: "尚未发现结构或运行问题。",
    validationDetail: "验证说明",
    constant: "常量",
    status: "草稿状态",
    openRun: "查看验证过程",
    remove: "移除节点",
  },
  en: {
    title: "Generate a workflow from one request",
    lead: "Describe the business outcome. Codex selects executable repository Agents, orders them, and wires compatible inputs and outputs.",
    requirement: "What business task should this workflow complete?",
    requirementPlaceholder: "Example: check a purchase order from goods receipt through invoice and clearing, then report missing stages and next actions.",
    compose: "Generate workflow draft",
    composing: "Matching Agents and compiling the workflow…",
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
    clarify: "Codex needs one key detail",
    clarifyPlaceholder: "Answer this question directly",
    continueCompose: "Continue",
    reconciled: "Current Agent catalog checked",
    catalogOnly: "Only executable repository Agents are selected, with version and digest pinned.",
    agents: "Available Agents",
    save: "Save draft",
    validate: "Validate live with Codex",
    publish: "Publish fixed workflow",
    acknowledge: "I acknowledge the completeness gaps in this validation",
    selectNode: "Select a node to configure its input mappings",
    mapping: "Input mapping",
    metadata: "Workflow details",
    workflowId: "Workflow ID",
    workflowName: "Workflow name",
    validationInputs: "Live validation input",
    autoDiscover: "Leave blank to discover a usable live candidate automatically",
    noIssues: "No structural or runtime issue is currently reported.",
    validationDetail: "Validation detail",
    constant: "Constant",
    status: "Draft status",
    openRun: "Open validation run",
    remove: "Remove node",
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

export default function WorkflowBuilder({ apiBase, locale, runPath, askPath }: BuilderProps) {
  const t = labels[locale];
  const [agents, setAgents] = useState<AgentDefinition[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [requirement, setRequirement] = useState("");
  const [clarificationInput, setClarificationInput] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [validationInputs, setValidationInputs] = useState<Record<string, string>>({});
  const pollTimer = useRef<number | null>(null);
  const reconciledDrafts = useRef(new Set<string>());

  const applyDraft = useCallback((value: Draft) => {
    setDraft(value);
    if (value.composition?.requirement) setRequirement(value.composition.requirement);
    if (value.composition?.validation_defaults) {
      setValidationInputs((current) => ({
        ...Object.fromEntries(Object.entries(value.composition.validation_defaults ?? {}).map(([key, item]) => [key, item == null ? "" : String(item)])),
        ...current,
      }));
    }
    if (!value.composition?.requirement) setAdvanced(true);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimer.current != null) window.clearInterval(pollTimer.current);
    pollTimer.current = null;
  }, []);

  const pollDraft = useCallback((draftId: string) => {
    stopPolling();
    pollTimer.current = window.setInterval(async () => {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}`);
      if (!response.ok) return;
      const value = (await response.json()) as Draft;
      applyDraft(value);
      if (value.status !== "planning" && value.validation?.live_status !== "running") stopPolling();
    }, 1000);
  }, [apiBase, applyDraft, stopPolling]);

  useEffect(() => {
    void (async () => {
      const response = await fetch(`${apiBase}/api/agents?executable=true`);
      const all = (await response.json()) as AgentDefinition[];
      setAgents(all.filter((agent) => Boolean(agent.execution?.outputSchema)));
      const requested = new URLSearchParams(window.location.search).get("draft");
      if (requested) {
        const existing = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(requested)}`);
        if (existing.ok) {
          const value = (await existing.json()) as Draft;
          applyDraft(value);
          if (value.status === "planning") pollDraft(value.draft_id);
        }
      }
    })().catch((error) => setMessage(String(error)));
    return stopPolling;
  }, [apiBase, applyDraft, pollDraft, stopPolling]);

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
    setDraft((current) => {
      if (!current) return current;
      const workflow = structuredClone(current.workflow);
      mutator(workflow);
      return { ...current, workflow, status: "draft" };
    });
  }, []);

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
      history.replaceState({}, "", `${window.location.pathname}?draft=${encodeURIComponent(payload.draft_id)}`);
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
      history.replaceState({}, "", `${window.location.pathname}?draft=${encodeURIComponent(payload.draft_id)}`);
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

  const validate = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
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
          input: Object.fromEntries(Object.entries(validationInputs)
            .filter(([, value]) => value.trim() !== "")
            .map(([name, value]) => [name, coerceValidationInput(value, current.workflow.inputSchema.properties[name]?.type)])),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Validation failed");
      applyDraft(payload as Draft); setMessage(`${t.validate} · ${payload.validation_run_id}`);
      pollDraft(payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const publish = async () => {
    if (!draft) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/publish`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ acknowledgeInconclusive: acknowledge }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail?.message ?? payload.detail ?? "Publish failed");
      applyDraft(payload as Draft); setMessage(`${t.publish} · ${payload.validation?.branch ?? ""}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const selected = draft?.workflow.nodes.find((node) => node.id === selectedNode);
  const selectedAgent = selected ? agentMap.get(selected.agentId) : undefined;
  const composition = draft?.composition;
  const gaps = composition?.gaps ?? [];
  const hasComposition = Boolean(composition?.requirement);
  const canValidate = Boolean(draft?.workflow.nodes.length) && gaps.length === 0 && draft?.status !== "planning" && draft?.status !== "waiting_input";

  return (
    <main className="workflow-builder-shell">
      <header className="workflow-builder-heading">
        <div><p className="eyebrow">Workflow Factory</p><h1>{t.title}</h1><p>{t.lead}</p></div>
        <div className="workflow-builder-actions">
          {draft && <button onClick={() => setAdvanced((value) => !value)}>{advanced ? t.hideAdvanced : t.advanced}</button>}
          {!draft && <button disabled={busy} onClick={createManualDraft}>{t.manual}</button>}
          {advanced && <button disabled={busy || !draft} onClick={save}>{t.save}</button>}
          <button disabled={busy || !canValidate} onClick={validate}>{t.validate}</button>
          <button disabled={busy || gaps.length > 0 || !["validated", "inconclusive"].includes(draft?.status ?? "")} onClick={publish}>{t.publish}</button>
        </div>
      </header>

      <section className="workflow-intent-panel">
        <label htmlFor="workflow-requirement"><strong>{t.requirement}</strong></label>
        <textarea id="workflow-requirement" rows={4} value={requirement} placeholder={t.requirementPlaceholder} onChange={(event) => setRequirement(event.target.value)} />
        <div className="workflow-intent-actions">
          <small>{t.catalogOnly}</small>
          <button disabled={busy || !requirement.trim()} onClick={compose}>{t.compose}</button>
        </div>
      </section>

      {draft?.status === "planning" && <section className="workflow-composition-state" aria-live="polite"><span className="workflow-spinner" />{t.composing}</section>}
      {composition?.error?.message && <section className="workflow-composition-state is-error" role="alert">{composition.error.message}</section>}

      {draft?.status === "waiting_input" && <section className="workflow-clarification-card">
        <p className="eyebrow">{t.clarify}</p>
        <h2>{composition?.clarification_question}</h2>
        <div><input value={clarificationInput} placeholder={t.clarifyPlaceholder} onChange={(event) => setClarificationInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void continueComposition(); }} /><button disabled={busy || !clarificationInput.trim()} onClick={continueComposition}>{t.continueCompose}</button></div>
      </section>}

      {hasComposition && draft?.status !== "planning" && draft?.status !== "waiting_input" && <section className="workflow-composition-summary">
        <div className="workflow-intent-summary"><span>{t.intent}</span><h2>{localizedText(composition?.intent, locale) || requirement}</h2></div>
        <div className="workflow-route-heading"><h2>{t.route}</h2><small>{draft?.workflow.nodes.length ?? 0} / {(composition?.stages ?? []).length} {t.matchedAgent}</small></div>
        <ol className="workflow-route-list">{(composition?.stages ?? []).map((stage, index) => {
          const gap = gaps.find((item) => item.stage_id === stage.id);
          const agent = stage.agent_id ? agentMap.get(stage.agent_id) : undefined;
          return <li className={gap ? "is-gap" : "is-matched"} key={stage.id}>
            <span className="workflow-route-index">{index + 1}</span>
            <div><strong>{localizedText(stage.capability, locale)}</strong><small>{gap ? t.missingAgent : `${t.matchedAgent} · ${agent?.title[locale] ?? stage.agent_id}`}</small></div>
          </li>;
        })}</ol>
      </section>}

      {gaps.length > 0 && <section className="workflow-gap-panel">
        <header><div><p className="eyebrow">Blocked</p><h2>{t.gapTitle}</h2><p>{t.gapHelp}</p></div><strong>{gaps.length}</strong></header>
        <div className="workflow-gap-list">{gaps.map((gap) => <article key={gap.gap_id}>
          <div><h3>{localizedText(gap.title, locale)}</h3><p>{localizedText(gap.description, locale)}</p></div>
          <dl><div><dt>Inputs</dt><dd>{gap.required_inputs.map((port) => `${port.name}: ${port.type}`).join(" · ") || "—"}</dd></div><div><dt>Outputs</dt><dd>{gap.required_outputs.map((port) => `${port.name}: ${port.type}`).join(" · ") || "—"}</dd></div></dl>
          {gap.agent_draft_id && <p className="workflow-gap-draft"><strong>{t.gapDraft}: {gap.agent_draft_id}</strong><br /><span>{t.awaitingCatalog}</span></p>}
          <a className="workflow-gap-action" href={`${askPath}?workflowDraft=${encodeURIComponent(draft!.draft_id)}&gap=${encodeURIComponent(gap.gap_id)}`}>{t.createGapAgent}</a>
        </article>)}</div>
      </section>}

      {advanced && draft && <section className="workflow-builder-grid">
        <aside className="workflow-agent-palette"><h2>{t.agents}</h2>{agents.map((agent) => <button key={agent.slug} onClick={() => addAgent(agent)}><strong>{agent.title[locale]}</strong><small>{agent.module} · {agent.slug}</small></button>)}</aside>
        <div className="workflow-canvas" aria-label={t.title}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onConnect={onConnect} onNodeClick={(_, node) => setSelectedNode(node.id)} onNodesChange={(changes) => mutateWorkflow((workflow) => { for (const change of changes) if (change.type === "position" && change.position) { const item = workflow.nodes.find((node) => node.id === change.id); if (item) item.position = change.position; } })} fitView>
            <Background /><MiniMap /><Controls />
          </ReactFlow>
        </div>
        <aside className="workflow-inspector">
          <h2>{t.metadata}</h2>
          <div className="workflow-metadata-fields">
            <label><span>{t.workflowId}</span><input value={draft.workflow.id} pattern="[a-z][a-z0-9-]*" onChange={(event) => mutateWorkflow((workflow) => { workflow.id = event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"); })} /></label>
            <label><span>{t.workflowName}</span><input value={draft.workflow.title[locale]} onChange={(event) => mutateWorkflow((workflow) => { workflow.title[locale] = event.target.value; })} /></label>
          </div>
          <h2>{t.mapping}</h2>{selected && selectedAgent ? <NodeInspector workflow={draft.workflow} nodeId={selected.id} agent={selectedAgent} agents={agentMap} locale={locale} onChange={mutateWorkflow} onRemove={() => { mutateWorkflow((workflow) => removeNode(workflow, selected.id)); setSelectedNode(null); }} removeLabel={t.remove} /> : <p>{t.selectNode}</p>}
        </aside>
      </section>}

      {draft && <section className="workflow-validation-panel">
        <strong>{t.status}: {draft.status}</strong>
        {draft.validation_run_id && <a href={`${runPath}?run=${encodeURIComponent(draft.validation_run_id)}`}>{t.openRun}</a>}
        <h2>{t.validationInputs}</h2>
        <p>{t.autoDiscover}</p>
        <div className="workflow-validation-inputs">{Object.entries(draft.workflow.inputSchema.properties ?? {}).filter(([, schema]) => schema["x-sapba-workflow-only"] !== true).map(([name, schema]) => <label key={name}><span>{schema.title?.[locale] ?? name}</span>{schema.type === "array" ? <textarea rows={4} value={validationInputs[name] ?? ""} placeholder={schema.placeholder?.[locale] ?? (locale === "zh" ? "每行或用逗号分隔" : "One per line or comma-separated")} onChange={(event) => setValidationInputs((current) => ({ ...current, [name]: event.target.value }))} /> : <input type={schema.format === "date" ? "date" : schema.type === "number" || schema.type === "integer" ? "number" : "text"} value={validationInputs[name] ?? ""} placeholder={schema.placeholder?.[locale] ?? name} onChange={(event) => setValidationInputs((current) => ({ ...current, [name]: event.target.value }))} />}</label>)}</div>
        <label><input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} />{t.acknowledge}</label>
        {message && <p role="status">{message}</p>}
        <h2>{t.validationDetail}</h2>
        <ul className="workflow-validation-issues">{validationMessages(draft.validation, locale).map((item) => <li key={item}>{item}</li>)}</ul>
      </section>}
      {!draft && message && <p className="workflow-builder-message" role="alert">{message}</p>}
    </main>
  );
}

function localizedText(value: { zh?: string; en?: string } | undefined, locale: Locale): string {
  return String(value?.[locale] || value?.zh || value?.en || "");
}

function coerceValidationInput(value: string, type?: ExecutionInputProperty["type"]): unknown {
  const scalarType = Array.isArray(type) ? type.find((item) => item !== "null") : type;
  if (scalarType === "array") return Array.from(new Set(value.split(/[\r\n,;，；]+/).map((item) => item.trim()).filter(Boolean)));
  if (scalarType === "integer") return Number.parseInt(value, 10);
  if (scalarType === "number") return Number(value);
  if (scalarType === "boolean") return value.toLowerCase() === "true";
  return value;
}

function validationMessages(validation: Record<string, unknown> | undefined, locale: Locale): string[] {
  if (!validation) return [];
  const messages: string[] = [];
  const issues = Array.isArray(validation.issues) ? validation.issues : [];
  for (const issue of issues) {
    if (typeof issue === "string") messages.push(issue);
    else if (issue && typeof issue === "object" && "message" in issue) messages.push(String(issue.message));
  }
  const review = validation.codex_review;
  if (review && typeof review === "object") {
    const localized = (review as Record<string, unknown>)[locale];
    const warning = (review as Record<string, unknown>).warning;
    if (localized) messages.push(String(localized));
    else if (warning) messages.push(String(warning));
  }
  if (validation.error && typeof validation.error === "object" && "message" in validation.error) messages.push(String(validation.error.message));
  if (!messages.length) messages.push(locale === "zh" ? "尚未发现结构或运行问题。" : "No structural or runtime issue is currently reported.");
  return messages;
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
