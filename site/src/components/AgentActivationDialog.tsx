import { useEffect, useRef, useState } from "react";

type Locale = "zh" | "en";
const activeStates = new Set(["starting", "queued", "running"]);
const phases = [
  ["checking_version", "检查版本", "Checking version"],
  ["preparing_worktree", "准备隔离工作区", "Preparing isolated workspace"],
  ["building_site", "构建并检查页面", "Building and checking pages"],
  ["saving_activation", "保存启用", "Saving activation"],
  ["refreshing_site", "刷新页面", "Refreshing pages"],
  ["completed", "完成", "Complete"],
] as const;

export default function AgentActivationDialog({ open, confirming, value, pending, currentVersion, locale, connectionError, onClose, onConfirm, onRetrySite }: {
  open: boolean; confirming: boolean; value: any; pending: any; currentVersion?: string | null; locale: Locale;
  connectionError: boolean; onClose: () => void; onConfirm: () => void; onRetrySite: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [now, setNow] = useState(Date.now());
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const active = !confirming && activeStates.has(String(value?.status || "starting"));
  useEffect(() => {
    if (!open) { if (dialog.current?.open) dialog.current.close(); return; }
    const previous = document.activeElement as HTMLElement | null;
    if (!dialog.current?.open) dialog.current?.showModal();
    return () => { if (dialog.current?.open) dialog.current.close(); previous?.focus(); };
  }, [open]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const started = Date.parse(value?.started_at || value?.created_at || "");
  const ended = Date.parse(value?.completed_at || "");
  const elapsed = Number.isFinite(started) ? Math.max(0, ((active ? now : Number.isFinite(ended) ? ended : now) - started) / 1000) : null;
  const phase = String(value?.phase || "checking_version");
  const refreshOnly = value?.kind === "site_refresh";
  const visiblePhases = refreshOnly ? phases.slice(-2) : phases;
  const phaseIndex = Math.max(0, visiblePhases.findIndex((item) => item[0] === phase));
  const activated = value?.activation_status === "active" || value?.result?.activation_status === "active";
  const siteFailed = activated && value?.site_refresh_status === "failed";
  const failureLabels: Record<string, [string, string]> = {
    git_main_required: ["正式工作目录当前不在 main；请先核对 Git 状态。", "The official checkout is not on main; review its Git state first."],
    git_worktree_dirty: ["正式工作目录存在未提交修改，暂不能启用。", "The official checkout has uncommitted changes; activation is unavailable."],
    agent_validation_pass_required: ["目标版本未通过验收，不能启用。", "The target version has not passed acceptance."],
    agent_activation_target_changed: ["目标版本或当前版本已变化，请刷新后重新核对。", "The target or current version changed. Refresh and review again."],
    agent_inactive_publication_required: ["该草稿没有对应的未启用发布结果。", "This draft has no matching inactive publication."],
    agent_activation_state_unconfirmed: ["上次启用提交结果尚未确认，请先核对 Git 状态。", "The previous activation commit is unconfirmed; inspect Git state first."],
  };
  const failureCode = String(value?.failure_code || "");
  return <dialog ref={dialog} className="sample-progress-dialog publication-progress-dialog" aria-labelledby="agent-activation-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><h2 id="agent-activation-title">{confirming ? tr("确认启用版本", "Confirm version activation") : active ? refreshOnly ? tr("正在刷新页面", "Refreshing pages") : tr("正在启用 Agent", "Activating Agent") : activated ? tr("Agent 已启用", "Agent activated") : tr("启用未完成", "Activation incomplete")}</h2><button type="button" className="agent-secondary-action" aria-label={tr("关闭对话框", "Close dialog")} onClick={onClose}>×</button></header>
    {confirming ? <>
      <p>{currentVersion ? `${tr("当前活动版本", "Current active version")} ${currentVersion} → ` : ""}{tr("待启用版本", "Version to activate")} {pending?.version}</p>
      <p>{tr("已有工作流继续固定引用原版本，不会自动升级。启用不重新验收，也不会调用 SAP 或自动推送代码。", "Existing workflows remain pinned to their original version. Activation does not rerun acceptance, call SAP, or push code.")}</p>
      <footer><button type="button" className="agent-secondary-action" onClick={onClose}>{tr("取消", "Cancel")}</button><button type="button" disabled={!pending?.can_activate} onClick={onConfirm}>{tr("确认启用", "Activate version")}</button></footer>
    </> : <>
      <p role="status">{active && <span className="sample-spinner" aria-hidden="true" />}{activated ? siteFailed ? tr("版本已启用，页面刷新失败", "Version activated; page refresh failed") : tr("版本已启用", "Version activated") : active ? tr("正在安全切换版本", "Safely switching versions") : tr("版本未启用", "Version was not activated")}</p>
      <p>{tr("目标版本", "Target version")}: {value?.version || pending?.version || "—"} · {tr("已耗时", "Elapsed")}: {elapsed == null ? tr("尚未开始", "Not started") : `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:${Math.floor(elapsed % 60).toString().padStart(2, "0")}`}</p>
      <ol className="publication-phase-list">{visiblePhases.map((item, index) => <li key={item[0]} className={index < phaseIndex || phase === "completed" ? "completed" : index === phaseIndex ? "current" : "pending"}>{tr(item[1], item[2])}</li>)}</ol>
      {siteFailed && <p className="agent-alert error" role="alert">{tr("启用已保存，当前仍显示上一可用页面。可单独重试刷新。", "Activation is saved, but the previous healthy pages remain visible. You can retry the page refresh alone.")}</p>}
      {!active && !activated && failureCode && <p className="agent-alert error" role="alert">{failureLabels[failureCode] ? tr(...failureLabels[failureCode]) : tr("启用未完成，请查看技术详情。", "Activation did not complete. See technical details.")}</p>}
      {value?.activation_status === "unknown" && <p className="agent-alert error" role="alert">{tr("提交结果尚不能确认，系统已阻止重复启用。", "The commit result is unconfirmed; duplicate activation is blocked.")}</p>}
      {connectionError && <p role="alert">{tr("进度连接异常，正在重连。", "Progress connection interrupted; reconnecting.")}</p>}
      {value?.failure_code && <details><summary>{tr("技术详情", "Technical details")}</summary><code>{value.failure_code}</code></details>}
      <footer>{active ? <button type="button" className="agent-secondary-action" onClick={onClose}>{tr("后台继续", "Continue in background")}</button> : <><button type="button" className="agent-secondary-action" onClick={onClose}>{tr("关闭", "Close")}</button>{siteFailed && <button type="button" onClick={onRetrySite}>{tr("重试刷新页面", "Retry page refresh")}</button>}</>}</footer>
    </>}
  </dialog>;
}
