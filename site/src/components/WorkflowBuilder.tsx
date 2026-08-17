import React, { useCallback, useEffect, useMemo, useState } from "react";
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
  ExecutionInputSchema,
  Locale,
  WorkflowConnectionDefinition,
  WorkflowDefinition,
} from "../lib/types";

type Draft = {
  draft_id: string;
  status: string;
  revision: number;
  workflow: WorkflowDefinition;
  validation_run_id?: string | null;
  validation: Record<string, unknown>;
};

type BuilderProps = { apiBase: string; locale: Locale; runPath: string };
type AgentNodeData = { agent: AgentDefinition; locale: Locale };

const labels = {
  zh: {
    title: "工作流编排",
    lead: "拖入固定 Agent，通过已声明的业务字段连接输入和输出。",
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
    title: "Workflow builder",
    lead: "Add fixed Agents and connect their declared business input and output ports.",
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
    schemaVersion: 1,
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
  const from = item.from.scope === "node_output" ? `${item.from.nodeId}:${item.from.port}` : `${item.from.scope}:${item.from.port ?? "value"}`;
  return `${from}->${item.to.nodeId}:${item.to.port}`;
}

export default function WorkflowBuilder({ apiBase, locale, runPath }: BuilderProps) {
  const t = labels[locale];
  const [agents, setAgents] = useState<AgentDefinition[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [validationInputs, setValidationInputs] = useState<Record<string, string>>({});

  useEffect(() => {
    void (async () => {
      const response = await fetch(`${apiBase}/api/agents?executable=true`);
      const all = (await response.json()) as AgentDefinition[];
      setAgents(all.filter((agent) => Boolean(agent.execution?.outputSchema)));
      const requested = new URLSearchParams(window.location.search).get("draft");
      if (requested) {
        const existing = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(requested)}`);
        if (existing.ok) setDraft((await existing.json()) as Draft);
        return;
      }
      const created = await fetch(`${apiBase}/api/authoring/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: emptyWorkflow().title, description: emptyWorkflow().description, workflow: emptyWorkflow() }),
      });
      if (created.ok) {
        const value = (await created.json()) as Draft;
        setDraft(value);
        history.replaceState({}, "", `${window.location.pathname}?draft=${encodeURIComponent(value.draft_id)}`);
      }
    })().catch((error) => setMessage(String(error)));
  }, [apiBase]);

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
        const inputName = uniqueInputName(workflow.inputSchema, port, nodeId);
        workflow.inputSchema.properties[inputName] = structuredClone(schema);
        workflow.inputSchema.required = [...(workflow.inputSchema.required ?? []), inputName];
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
      setDraft(payload as Draft); setMessage(`${t.save} · r${payload.revision}`);
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
        current = savedPayload as Draft; setDraft(current);
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
      setDraft(payload as Draft); setMessage(`${t.validate} · ${payload.validation_run_id}`);
      pollDraft(payload.draft_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const pollDraft = (draftId: string) => {
    const timer = window.setInterval(async () => {
      const response = await fetch(`${apiBase}/api/authoring/workflows/${encodeURIComponent(draftId)}`);
      if (!response.ok) return;
      const value = (await response.json()) as Draft;
      setDraft(value);
      if (["validated", "inconclusive", "invalid", "published"].includes(value.status) || value.validation.live_status === "repair_failed") window.clearInterval(timer);
    }, 1000);
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
      setDraft(payload as Draft); setMessage(`${t.publish} · ${payload.validation?.branch ?? ""}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };

  const selected = draft?.workflow.nodes.find((node) => node.id === selectedNode);
  const selectedAgent = selected ? agentMap.get(selected.agentId) : undefined;

  return (
    <main className="workflow-builder-shell">
      <header className="workflow-builder-heading"><div><p className="eyebrow">Workflow Factory</p><h1>{t.title}</h1><p>{t.lead}</p></div><div className="workflow-builder-actions"><button disabled={busy || !draft} onClick={save}>{t.save}</button><button disabled={busy || !draft?.workflow.nodes.length} onClick={validate}>{t.validate}</button><button disabled={busy || !["validated", "inconclusive"].includes(draft?.status ?? "")} onClick={publish}>{t.publish}</button></div></header>
      <section className="workflow-builder-grid">
        <aside className="workflow-agent-palette"><h2>{t.agents}</h2>{agents.map((agent) => <button key={agent.slug} onClick={() => addAgent(agent)}><strong>{agent.title[locale]}</strong><small>{agent.module} · {agent.slug}</small></button>)}</aside>
        <div className="workflow-canvas" aria-label={t.title}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onConnect={onConnect} onNodeClick={(_, node) => setSelectedNode(node.id)} onNodesChange={(changes) => mutateWorkflow((workflow) => { for (const change of changes) if (change.type === "position" && change.position) { const item = workflow.nodes.find((node) => node.id === change.id); if (item) item.position = change.position; } })} fitView>
            <Background /><MiniMap /><Controls />
          </ReactFlow>
        </div>
        <aside className="workflow-inspector">
          <h2>{t.metadata}</h2>
          {draft && <div className="workflow-metadata-fields">
            <label><span>{t.workflowId}</span><input value={draft.workflow.id} pattern="[a-z][a-z0-9-]*" onChange={(event) => mutateWorkflow((workflow) => { workflow.id = event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"); })} /></label>
            <label><span>{t.workflowName}</span><input value={draft.workflow.title[locale]} onChange={(event) => mutateWorkflow((workflow) => { workflow.title[locale] = event.target.value; })} /></label>
          </div>}
          <h2>{t.mapping}</h2>{selected && selectedAgent ? <NodeInspector workflow={draft!.workflow} nodeId={selected.id} agent={selectedAgent} agents={agentMap} locale={locale} onChange={mutateWorkflow} onRemove={() => { mutateWorkflow((workflow) => removeNode(workflow, selected.id)); setSelectedNode(null); }} removeLabel={t.remove} /> : <p>{t.selectNode}</p>}
        </aside>
      </section>
      <section className="workflow-validation-panel">
        <strong>{t.status}: {draft?.status ?? "loading"}</strong>
        {draft?.validation_run_id && <a href={`${runPath}?run=${encodeURIComponent(draft.validation_run_id)}`}>{t.openRun}</a>}
        <h2>{t.validationInputs}</h2>
        <p>{t.autoDiscover}</p>
        <div className="workflow-validation-inputs">{Object.entries(draft?.workflow.inputSchema.properties ?? {}).map(([name, schema]) => <label key={name}><span>{schema.title?.[locale] ?? name}</span><input type={schema.format === "date" ? "date" : schema.type === "number" || schema.type === "integer" ? "number" : "text"} value={validationInputs[name] ?? ""} placeholder={schema.placeholder?.[locale] ?? name} onChange={(event) => setValidationInputs((current) => ({ ...current, [name]: event.target.value }))} /></label>)}</div>
        <label><input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} />{t.acknowledge}</label>
        {message && <p>{message}</p>}
        <h2>{t.validationDetail}</h2>
        <ul className="workflow-validation-issues">{validationMessages(draft?.validation, locale).map((item) => <li key={item}>{item}</li>)}</ul>
      </section>
    </main>
  );
}

function coerceValidationInput(value: string, type?: string): unknown {
  if (type === "integer") return Number.parseInt(value, 10);
  if (type === "number") return Number(value);
  if (type === "boolean") return value.toLowerCase() === "true";
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
  const mappings = Object.keys(agent.execution?.inputSchema.properties ?? {});
  const options = workflow.nodes.flatMap((node) => {
    if (node.id === nodeId) return [];
    const sourceAgent = agents.get(node.agentId);
    return Object.keys(sourceAgent?.execution?.outputSchema?.properties ?? {}).map((port) => ({ value: `${node.id}:${port}`, label: `${sourceAgent?.title[locale]} · ${port}` }));
  });
  return <div>{mappings.map((port) => {
    const connection = workflow.connections.find((item) => item.to.nodeId === nodeId && item.to.port === port);
    const value = connection?.from.scope === "node_output" ? `${connection.from.nodeId}:${connection.from.port}` : connection?.from.scope === "workflow_input" ? `input:${connection.from.port}` : "constant";
    return <label className="workflow-mapping-row" key={port}><span>{port}</span><select value={value} onChange={(event) => onChange((next) => {
      removeTargetMapping(next, nodeId, port, false);
      if (event.target.value.startsWith("input:")) next.connections.push({ from: { scope: "workflow_input", port: event.target.value.slice(6) }, to: { nodeId, port }, transform: { type: "identity" } });
      else if (event.target.value === "constant") next.connections.push({ from: { scope: "constant", value: "" }, to: { nodeId, port }, transform: { type: "identity" } });
      else { const [sourceNode, sourcePort] = event.target.value.split(":"); next.connections.push({ from: { scope: "node_output", nodeId: sourceNode, port: sourcePort }, to: { nodeId, port }, transform: { type: "identity" } }); }
    })}>{Object.keys(workflow.inputSchema.properties).map((name) => <option key={name} value={`input:${name}`}>{name}</option>)}{options.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}<option value="constant">Constant</option></select>{connection?.from.scope === "constant" && <input value={String(connection.from.value ?? "")} onChange={(event) => onChange((next) => { const item = next.connections.find((entry) => entry.to.nodeId === nodeId && entry.to.port === port); if (item) item.from.value = event.target.value; })} />}</label>;
  })}<button className="danger-button" onClick={onRemove}>{removeLabel}</button></div>;
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
  workflow.outputs = workflow.outputs.filter((item) => !(item.source.scope === "node_output" && item.source.nodeId === nodeId));
  for (const name of Object.keys(workflow.outputSchema.properties).filter((name) => name.startsWith(`${nodeId}_`))) { delete workflow.outputSchema.properties[name]; workflow.outputSchema.required = (workflow.outputSchema.required ?? []).filter((item) => item !== name); }
}
