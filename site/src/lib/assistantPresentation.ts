/** Presentation only: never used to authorize or apply a candidate. */
export const businessSummaryVersion = "business-summary/1";

export function assistantWidth(preferred: number, available: number, viewport: number) {
  const max = Math.max(0, Math.min(640, available));
  return { width: viewport <= 550 ? available : Math.min(max, Math.max(320, preferred)),
    max, resizable: viewport > 550 && max > 320 };
}

export function resultPresentation(value: {
  status: string; intent?: string; action?: string; version?: string; reply?: string;
  baseRevision?: number; resultRevision?: number; diff?: unknown[];
}, locale: string) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const explain = value.intent === "explain" || ["reply", "explain", "clarify"].includes(value.action || "");
  // A model's answer is not a save receipt, especially when validation failed later.
  if (value.status !== "completed") return { text: "", saved: false };
  if (explain) return { text: value.reply || "", saved: false };
  const saved = Number.isInteger(value.resultRevision) && Number.isInteger(value.baseRevision)
    && value.resultRevision! > value.baseRevision! && Boolean(value.diff?.length);
  if (value.version === businessSummaryVersion && saved) return { text: value.reply || "", saved };
  if (saved) return { saved, text: tr("已保存本轮修改。具体变更可通过下方入口查看；旧记录未提供独立的业务摘要。", "This turn's changes were saved. Review them below; the historical record has no separate business summary.") };
  if (value.resultRevision === value.baseRevision && Array.isArray(value.diff) && !value.diff.length)
    return { saved: false, text: tr("本轮已完成，未保存新的内容修改。", "Completed without saving new content changes.") };
  return { saved: false, text: tr("本轮已结束，但保存依据不足，无法恢复详细结果摘要。", "This turn ended, but there is insufficient save evidence to reconstruct a detailed result summary.") };
}
