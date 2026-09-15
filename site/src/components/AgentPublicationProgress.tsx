import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";

const activeStates = new Set(["starting", "queued", "running", "cancelling"]);
const phases = [
  ["preparing", "准备发布", "Preparing publication"],
  ["preparing_worktree", "检查资料", "Checking package"],
  ["building_site", "构建页面", "Building pages"],
  ["checking_site", "检查页面", "Checking pages"],
  ["saving_publication", "保存发布", "Saving publication"],
  ["refreshing_site", "刷新页面", "Refreshing pages"],
  ["completed", "完成", "Complete"],
] as const;

const formatTime = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
const timestamp = (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;

export function PublicationProgressSummary({ value, locale, connectionError = false }: { value: any; locale: Locale; connectionError?: boolean }) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const status = String(value?.status || "starting");
  const active = activeStates.has(status);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const started = timestamp(value?.started_at || value?.created_at);
  const completed = timestamp(value?.completed_at);
  const elapsed = started === null ? null : Math.max(0, ((active ? now : completed ?? now) - started) / 1000);
  const phase = String(value?.phase || (active ? "preparing" : "completed"));
  const index = Math.max(0, phases.findIndex((item) => item[0] === phase));
  const publication = String(value?.publication_status || "pending");
  const site = String(value?.site_refresh_status || "pending");
  const outcome = publication === "published" && site === "failed"
    ? tr("Agent已发布，页面刷新失败", "Agent published; page refresh failed")
    : publication === "published" && ["completed", "not_required"].includes(site)
      ? tr("Agent发布已完成", "Agent publication completed")
      : status === "failed"
        ? tr("Agent发布未完成", "Agent publication did not complete")
        : tr(phases[index][1], phases[index][2]);
  return <div className="publication-progress-summary">
    <p role="status">{active && <span className="sample-spinner" aria-hidden="true" />}{outcome}</p>
    <p>{tr("目标版本", "Target version")}: {value?.version || "—"} · {tr("已耗时", "Elapsed")}: {elapsed == null ? tr("尚未开始", "Not started") : formatTime(elapsed)}</p>
    <ol className="publication-phase-list">{phases.map((item, phaseIndex) => <li key={item[0]} className={phaseIndex < index || phase === "completed" ? "completed" : phaseIndex === index ? "current" : "pending"}>{tr(item[1], item[2])}</li>)}</ol>
    {publication === "published" && site === "failed" && <p className="agent-alert error" role="alert">{tr("Agent已发布并保留启用结果；当前前台仍显示上一可用页面。可单独重试刷新，不会创建新版本或Git提交。", "The Agent is published and its activation is retained. The previous healthy pages are still served. Retry only the page refresh; no new version or Git commit will be created.")}</p>}
    {publication === "failed" && <p className="agent-alert error" role="alert">{tr("发布提交未合并到main。草稿仍可修正后重试。", "The publication commit was not merged into main. The draft remains available for correction and retry.")}</p>}
    {publication === "unknown" && <p className="agent-alert error" role="alert">{tr("无法确认发布提交与草稿记录是否一致。系统已阻止重复发布，请先检查技术详情。", "The publication commit and draft record could not be reconciled. Duplicate publication is blocked; inspect the technical details first.")}</p>}
    {connectionError && <p role="alert">{tr("进度连接异常，正在重连；已保存的发布任务不会丢失。", "Progress connection was interrupted. Reconnecting; the saved publication task is retained.")}</p>}
    {(value?.commit_sha || value?.build_fingerprint || value?.failure_code) && <details><summary>{tr("技术详情", "Technical details")}</summary><dl><dt>Commit</dt><dd><code>{value?.commit_sha || "—"}</code></dd><dt>{tr("构建指纹", "Build fingerprint")}</dt><dd><code>{value?.build_fingerprint || "—"}</code></dd><dt>{tr("失败代码", "Failure code")}</dt><dd><code>{value?.failure_code || "—"}</code></dd></dl></details>}
  </div>;
}

export default function AgentPublicationProgress({ open, value, locale, connectionError, onClose, onRetrySite, onComplete }: {
  open: boolean; value: any; locale: Locale; connectionError?: boolean;
  onClose: () => void; onRetrySite: () => void; onComplete: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const active = activeStates.has(String(value?.status || "starting"));
  const publication = String(value?.publication_status || "pending");
  const site = String(value?.site_refresh_status || "pending");
  useEffect(() => {
    if (!open) { if (dialog.current?.open) dialog.current.close(); return; }
    const previous = document.activeElement as HTMLElement | null;
    if (!dialog.current?.open) dialog.current?.showModal();
    return () => { if (dialog.current?.open) dialog.current.close(); previous?.focus(); };
  }, [open]);
  const title = active ? tr("正在发布Agent", "Publishing Agent")
    : publication === "published" && site === "failed" ? tr("Agent已发布，页面待刷新", "Agent published; pages need refresh")
    : publication === "published" ? tr("Agent发布完成", "Agent publication complete")
    : tr("Agent发布结果", "Agent publication result");
  return <dialog ref={dialog} className="sample-progress-dialog publication-progress-dialog" aria-labelledby="publication-progress-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><h2 id="publication-progress-title">{title}</h2><button type="button" className="agent-secondary-action" aria-label={tr("关闭对话框，后台继续", "Close dialog and continue in background")} onClick={onClose}>×</button></header>
    <p>{tr("系统先在隔离工作区检查并构建页面，再把单次发布提交快进合并到本地main。不会自动推送远端。", "The package and pages are checked in an isolated worktree before the single publication commit is fast-forwarded into local main. Nothing is pushed automatically.")}</p>
    <PublicationProgressSummary value={value} locale={locale} connectionError={connectionError} />
    <footer>{active ? <button type="button" className="agent-secondary-action" onClick={onClose}>{tr("后台继续", "Continue in background")}</button>
      : publication === "published" && site === "failed" ? <><button type="button" className="agent-secondary-action" onClick={onComplete}>{tr("返回管理列表", "Back to management")}</button><button type="button" onClick={onRetrySite}>{tr("重试刷新页面", "Retry page refresh")}</button></>
      : publication === "published" ? <button type="button" onClick={onComplete}>{tr("完成", "Done")}</button>
      : <button type="button" onClick={onClose}>{tr("返回发布页面", "Return to publication")}</button>}</footer>
  </dialog>;
}
