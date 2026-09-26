import { useEffect, useRef, useState } from "react";
import SafeMarkdown from "./SafeMarkdown";
import type { Locale } from "../lib/types";

type Round = { request_id: string; status: string; intent: "explain" | "revise"; message: string;
  answer?: string; question?: string; clarification_id?: string; base_revision: number; result_revision?: number;
  failure_code?: string; diff?: unknown[]; binding_changes?: boolean; runtime?: unknown; capabilities?: unknown };
type Snapshot = { rounds: Round[]; events: { id: number; phase: string; request_id: string }[] };
type Props = { apiBase: string; locale: Locale; draftId?: string; revision?: number; step: string;
  fieldPath?: string; published: boolean; dirty: boolean; busy: boolean;
  prepare: (intent: "explain" | "revise", requestId: string) => Promise<{ draft_id: string; revision: number }>;
  refresh: () => void };

export function failureMessage(code: string, zh: boolean): string {
  const messages: Record<string, [string, string]> = {
    agent_harness_sandbox_preflight_timeout: ["Windows 受限工作区预检超时，尚未调用模型。请核对部署账户的 Codex 沙盒配置；系统未自动降级权限。", "Windows restricted-workspace preflight timed out before model execution. Check Codex sandbox setup for the service account; permissions were not downgraded."],
    agent_harness_sandbox_preflight_failed: ["Windows 沙盒边界检查未通过，本轮已阻止执行。", "Windows sandbox boundary checks failed; this round was blocked."],
    workflow_harness_source_boundary_failed: ["源码只读边界检查未通过，本轮已阻止执行。", "The read-only source boundary check failed; this round was blocked."],
    workflow_assistant_timeout: ["本轮执行超时，请查看进度及清理状态。", "This round timed out. Check progress and cleanup status."],
    workflow_revision_conflict: ["工作流已变化，请核对当前修订后重新提交。", "The workflow changed. Review the current revision before resubmitting."],
  };
  return messages[code]?.[zh ? 0 : 1] ?? code;
}

export default function WorkflowAssistant(props: Props) {
  const zh = props.locale === "zh";
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"restricted" | "full_access">("restricted");
  const [snapshot, setSnapshot] = useState<Snapshot>({ rounds: [], events: [] });
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const pending = useRef<{ url: string; body: Record<string, unknown> } | null>(null);
  const drawer = useRef<HTMLElement | null>(null);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const cursor = useRef(0);
  const refreshRef = useRef(props.refresh);
  refreshRef.current = props.refresh;
  const reply = snapshot.rounds.find(r => r.status === "waiting_input");
  const running = snapshot.rounds.find(r => ["running", "cancelling", "cleanup_pending"].includes(r.status));

  useEffect(() => {
    if (!open) return;
    drawer.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); toggle.current?.focus(); }
      if (event.key === "Tab" && window.matchMedia("(max-width: 1199px)").matches) {
        const elements = drawer.current?.querySelectorAll<HTMLElement>("button:not(:disabled), textarea, select, a[href], summary");
        if (!elements?.length) return;
        const first = elements[0], last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", keyboard);
    return () => document.removeEventListener("keydown", keyboard);
  }, [open]);

  useEffect(() => {
    cursor.current = 0;
    setSnapshot({ rounds: [], events: [] });
    if (!props.draftId) return;
    let closed = false;
    let timer: number | undefined;
    let failures = 0;
    const base = `${props.apiBase}/api/authoring/workflows/${encodeURIComponent(props.draftId)}/assistant`;
    const load = async () => {
      try {
        const response = await fetch(base);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value = await response.json() as Snapshot;
        if (closed) return;
        setSnapshot(value);
        cursor.current = value.events.at(-1)?.id ?? cursor.current;
        refreshRef.current();
        failures = 0;
        return value;
      } catch { failures += 1; }
    };
    const events = new EventSource(`${base}/events?after=${cursor.current}`);
    events.addEventListener("workflow_assistant", () => { void load(); });
    events.onerror = () => {
      events.close();
      const poll = async () => {
        const value = await load();
        if (!closed) {
          const active = !value || value.rounds.some(r => ["running", "queued", "cancelling"].includes(r.status));
          timer = window.setTimeout(poll, active ? Math.min(30000, 2000 * 2 ** Math.min(failures, 4)) : 30000);
        }
      };
      void poll();
    };
    void load();
    return () => { closed = true; events.close(); window.clearTimeout(timer); };
  }, [props.apiBase, props.draftId]);

  const send = async (intent: "explain" | "revise") => {
    if (!text.trim() && !pending.current) return;
    setSending(true); setError("");
    try {
      if (!pending.current) {
        if (mode === "full_access" && !window.confirm(zh
          ? "可信本地模式允许原生命令，工作副本不是操作系统沙箱。平台工具仍受授权控制。确认使用？"
          : "Trusted local mode permits native commands and is not an OS sandbox. Platform tools remain authorized. Continue?")) return;
        const requestId = crypto.randomUUID();
        const draft = await props.prepare(intent, requestId);
        const body = { requestId, baseTurn: 1, baseRevision: draft.revision, feedback: text.trim(), locale: props.locale,
          intent, executionMode: mode, trustedLocalConfirmed: mode === "full_access", budgetSeconds: 600,
          replyToClarificationId: reply?.clarification_id ?? null,
          references: props.fieldPath && !props.dirty ? [{ kind: "workflow_field", draftId: draft.draft_id, revision: draft.revision, path: props.fieldPath }] : [] };
        pending.current = { url: `${props.apiBase}/api/authoring/workflows/${encodeURIComponent(draft.draft_id)}/feedback`, body };
        sessionStorage.setItem(`workflow-assistant-pending:${draft.draft_id}`, JSON.stringify(pending.current));
      }
      const response = await fetch(pending.current.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending.current.body) });
      const value = await response.json();
      if (!response.ok) {
        // A definitive rejection is not an uncertain transport result.
        pending.current = null;
        if (props.draftId) sessionStorage.removeItem(`workflow-assistant-pending:${props.draftId}`);
        throw new Error(value.detail?.code ?? value.detail?.message ?? "Request rejected");
      }
      sessionStorage.removeItem(`workflow-assistant-pending:${value.draft_id}`);
      pending.current = null; setText("");
      refreshRef.current();
      const state = await fetch(`${props.apiBase}/api/authoring/workflows/${value.draft_id}/assistant`);
      if (state.ok) setSnapshot(await state.json());
    } catch (reason) { setError(String(reason)); }
    finally { setSending(false); }
  };

  useEffect(() => {
    pending.current = null;
    if (!props.draftId) return;
    const saved = sessionStorage.getItem(`workflow-assistant-pending:${props.draftId}`);
    if (saved) {
      try { pending.current = JSON.parse(saved); setError(zh ? "上次响应未确认；只能重传原请求。" : "Previous response is uncertain; retry the original request."); }
      catch { sessionStorage.removeItem(`workflow-assistant-pending:${props.draftId}`); }
    }
  }, [props.draftId, zh]);

  const cancel = async (round: Round) => {
    const response = await fetch(`${props.apiBase}/api/authoring/workflows/${props.draftId}/assistant/${encodeURIComponent(round.request_id)}/cancel`, { method: "POST" });
    if (!response.ok) setError(zh ? "取消未确认，请刷新核对。" : "Cancellation unconfirmed; refresh to check.");
    else refreshRef.current();
  };
  return <>
    <button ref={toggle} className="workflow-assistant-toggle" onClick={() => setOpen(true)}>{zh ? "AI 助手" : "AI assistant"}</button>
    <aside ref={drawer} className={`workflow-assistant-v2${open ? " is-open" : ""}`} aria-label={zh ? "工作流 AI 助手" : "Workflow AI assistant"}>
      <header><h2>{zh ? "AI 助手" : "AI assistant"}</h2><button onClick={() => setOpen(false)} aria-label={zh ? "关闭助手，后台继续" : "Close assistant; continue in background"}>×</button></header>
      <p>{props.draftId ?? (zh ? "首次发送时创建草稿" : "Draft created on first message")} · r{props.revision ?? "—"} · {props.step}</p>
      <p>{zh ? "以已保存定义为准；解释不修改，修改需重新审核。上下文由平台承接。" : "Uses the saved definition. Explain is read-only; revisions require review. Context is reconstructed by the platform."}</p>
      {props.fieldPath && <code>{props.fieldPath}</code>}
      <div className="workflow-assistant-rounds" aria-live="polite">{snapshot.rounds.map(round => <article key={round.request_id}>
        <strong>r{round.base_revision}{round.result_revision && ` → r${round.result_revision}`} · {round.intent} · {round.status}</strong>
        <p>{round.message}</p><SafeMarkdown text={round.answer ?? round.question ?? ""} />
        {round.question && <p>{round.question}</p>}
        {round.failure_code && <p role="alert">{failureMessage(round.failure_code, zh)}</p>}
        {round.binding_changes && <p role="alert">{zh ? "本轮改变了固定 Agent 或连接绑定，必须重新确认设计与验证。" : "Fixed Agent or connection bindings changed; design confirmation and validation are required again."}</p>}
        {round.status === "needs_review" && <button onClick={() => {
          if (window.confirm(zh ? "核对当前修订后，将原消息作为新请求提交？" : "Review the current revision and reuse this message as a new request?")) {
            setText(round.message); setError("");
          }
        }}>{zh ? "核对后重新填写" : "Review and reuse message"}</button>}
        {round.diff && round.diff.length > 0 && <details><summary>{zh ? "本轮 Diff" : "This turn's Diff"}</summary><pre>{JSON.stringify(round.diff, null, 2)}</pre></details>}
        <details><summary>{zh ? "技术详情" : "Technical details"}</summary><pre>{JSON.stringify({ failure_code: round.failure_code, runtime: round.runtime, capabilities: round.capabilities }, null, 2)}</pre></details>
        {["queued", "needs_review", "waiting_input", "running"].includes(round.status) && <button onClick={() => void cancel(round)}>{zh ? "撤回／取消" : "Withdraw / cancel"}</button>}
      </article>)}</div>
      {running && <p role="status">{snapshot.events.filter(e => e.request_id === running.request_id).at(-1)?.phase} · {zh ? "关闭后继续执行" : "Continues when closed"}</p>}
      {running?.status === "cleanup_pending" && <p role="alert">{zh ? "旧 SDK 任务清理尚未确认，暂不派发新消息。请核对服务进程。" : "SDK cleanup is unconfirmed. New messages remain blocked; check service processes."}</p>}
      <label>{zh ? "请求或补充说明" : "Request or clarification"}<textarea rows={4} value={text} onChange={e => setText(e.target.value)} /></label>
      <label>{zh ? "执行模式" : "Execution mode"}<select value={mode} disabled={Boolean(pending.current)} onChange={e => setMode(e.target.value as typeof mode)}><option value="restricted">{zh ? "受限工作区（默认）" : "Restricted workspace (default)"}</option><option value="full_access">{zh ? "可信本地 full_access" : "Trusted local full_access"}</option></select></label>
      {error && <p role="alert">{error}</p>}
      <div className="workflow-assistant-buttons">
        <button disabled={sending || props.busy || (!text.trim() && !pending.current) || Boolean(pending.current && pending.current.body.intent !== "explain") || Boolean(reply && reply.intent !== "explain")} onClick={() => void send("explain")}>{zh ? "解释问题" : "Explain"}</button>
        <button disabled={sending || props.busy || props.published || (!text.trim() && !pending.current) || Boolean(pending.current && pending.current.body.intent !== "revise") || Boolean(reply && reply.intent !== "revise")} onClick={() => void send("revise")}>{zh ? props.dirty ? "保存并修改" : "修改工作流" : props.dirty ? "Save and revise" : "Revise workflow"}</button>
      </div>
    </aside>
  </>;
}
