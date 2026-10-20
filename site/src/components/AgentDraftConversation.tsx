import { useEffect, useState } from "react";
import type { Locale } from "../lib/types";
import { canRetryFeedback, draftStatus, feedbackDuration, feedbackFailureText, feedbackTiming, feedbackValidationIssues, localText } from "../lib/agentDraft";
import { resultPresentation } from "../lib/assistantPresentation";
import SafeMarkdown from "./SafeMarkdown";
import AssistantRound from "./AssistantRound";
import AssistantHistory from "./AssistantHistory";
import type { FeedbackObjectReference } from "../lib/agentDraft";

const auditKinds: Record<string, [string, string]> = {
  initial: ["创建草稿", "Draft created"], create: ["创建草稿", "Draft created"], clone: ["复制现有Agent", "Agent copied"],
  manual_edit: ["保存表单修改", "Form changes saved"], undo: ["恢复历史修订", "Revision restored"], import: ["导入草稿", "Draft imported"],
  technical_id: ["确认技术 ID", "Technical ID confirmed"], technical_id_confirmed: ["确认技术 ID", "Technical ID confirmed"], technical_id_renamed: ["修改技术 ID", "Technical ID changed"],
};

export default function AgentDraftConversation({ turns, locale, onRetry, onWithdraw, currentRevision, retryDisabled = false }: { turns: any[]; locale: Locale; onRetry?: (turn: any) => void; onWithdraw?: (turn: any) => void; onLocate?: (reference: FeedbackObjectReference) => void; currentRevision?: number; diagnostics?: any[]; retryDisabled?: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  const hasActiveTurn = turns.some(turn => feedbackTiming(turn).active);
  useEffect(() => {
    if (!hasActiveTurn) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveTurn]);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const latest = [...turns].reverse().find(turn => turn.kind === "feedback") || turns.at(-1);
  let number = 0;
  return <div className="draft-conversation-history"><AssistantHistory locale={locale} entries={turns.map((turn, index) => {
    const audit = auditKinds[turn.kind];
    if (!audit) number++;
    const title = audit ? audit[locale === "zh" ? 0 : 1] : tr(`第${number}轮对话`, `Conversation ${number}`);
    const timing = feedbackTiming(turn, now);
    const failed = ["failed", "cancelled", "interrupted", "timed_out", "expired"].includes(turn.status);
    const failureCode = turn.decision?.error_code || (turn.status === "cancelled" ? "agent_feedback_cancelled" : turn.status === "interrupted" ? "agent_feedback_interrupted" : turn.status === "timed_out" ? "agent_feedback_timeout" : "");
    const issues = failed ? feedbackValidationIssues(turn, locale) : [];
    const question = turn.decision?.pending_clarification;
    const answered = question && turns.some(later => ["completed", "queued", "running", "waiting"].includes(later.status) && later.decision?.reply_to_clarification_id === question.id);
    const expired = question && currentRevision !== undefined && question.revision !== currentRevision;
    const reply = localText(turn.assistant_message || turn.decision?.assistant_message || turn.decision?.summary, locale);
    const result = resultPresentation({ status: turn.status, action: turn.decision?.action, intent: turn.decision?.intent,
      version: turn.decision?.presentation_version, reply, baseRevision: turn.base_revision, resultRevision: turn.result_revision, diff: turn.diff }, locale);
    const identityChange = turn.diff?.find((change: any) => change.path === "/manifest/slug");
    return { id: String(turn.turn ?? turn.turn_number ?? index), latest: turn === latest, label: title, audit: Boolean(audit),
      attention: timing.active || turn.status === "needs_review" || Boolean(question && !answered && !expired),
      content: <AssistantRound latest={turn === latest} className={audit ? "draft-audit-entry" : "draft-chat-entry"}
        summary={<strong>{title}{!audit && <> · {draftStatus(turn.status || turn.decision?.action, locale)}</>}</strong>}>
        {audit ? <p>{identityChange ? <>{tr("技术 ID", "Technical ID")}: <code>{String(identityChange.before ?? "")}</code> → <code>{String(identityChange.after ?? "")}</code></> : title}</p> : <>
          <p className="draft-user-message">{turn.user_message || turn.feedback || tr("历史用户消息未记录", "Historical user message was not recorded")}</p>
          {question && <div className="draft-pending-question"><strong>{answered ? tr("已回复的澄清问题", "Answered clarification") : expired ? tr("已过期的澄清问题", "Expired clarification") : tr("待确认问题", "Question to confirm")}</strong>
            <SafeMarkdown text={localText(question.question, locale)} />
            {question.options?.length > 0 && <ul>{question.options.map((option: any) => <li key={option.id}>{localText(option.label, locale)}</li>)}</ul>}
          </div>}
          {timing.active && <p role="status">{turn.status === "waiting" ? tr("消息已排队，等待当前回合结束。", "Queued behind the current turn.") : tr("正在处理", "Processing")} · {tr("已等待", "Elapsed")} {feedbackDuration(timing.elapsed_seconds, locale)}</p>}
          {failed ? <p className="draft-feedback-failure" role="alert">{feedbackFailureText(failureCode, locale)}</p>
            : turn.status === "needs_review" ? <p>{tr("前一回合改变了状态，请核对后重新发送。", "The previous turn changed the state. Review and resend.")}</p>
            : !question && result.text ? <SafeMarkdown text={result.text} /> : null}
          {result.saved && <p className="assistant-save-result">{tr("修改已保存，尚未发布；需要按当前修订重新核对试运行和验收状态。", "Changes saved, not published. Review trial and acceptance status for the current revision.")}</p>}
          {issues.length > 0 && <div className="draft-feedback-validation"><h4>{tr("定义校验未通过", "Definition validation failed")}</h4><ul>{issues.map((issue, i) => <li key={i}>{issue.text}</li>)}</ul></div>}
          {timing.deadline_reached && <p role="status">{tr("已到记录的截止时间，正在等待服务确认状态；请勿重复发送。", "The recorded deadline has passed. Waiting for server confirmation; do not send a duplicate request.")}</p>}
          {canRetryFeedback(turn) && onRetry && <button type="button" className="agent-secondary-action" disabled={retryDisabled} onClick={() => onRetry(turn)}>{tr("重试这条意见", "Retry this feedback")}</button>}
          {turn.status === "waiting" && onWithdraw && <button type="button" className="agent-secondary-action" onClick={() => onWithdraw(turn)}>{tr("撤回排队消息", "Withdraw queued message")}</button>}
          {turn.status === "needs_review" && onRetry && <button type="button" className="agent-secondary-action" disabled={retryDisabled} onClick={() => onRetry(turn)}>{tr("核对并重新发送", "Review and resend")}</button>}
          {Array.isArray(turn.diff) && turn.diff.length > 0 && <details className="draft-turn-diff"><summary>{tr("查看本轮修改", "View changes in this turn")} · {tr("修订", "revision")} {turn.base_revision} → {turn.result_revision}</summary><ul>{turn.diff.map((change: any, i: number) => <li key={i}><code>{change.path}</code><pre>{JSON.stringify({ before: change.before, after: change.after }, null, 2)}</pre></li>)}</ul></details>}
        </>}
      </AssistantRound> };
  })} /></div>;
}
