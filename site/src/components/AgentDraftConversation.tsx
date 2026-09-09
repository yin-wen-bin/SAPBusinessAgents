import type { Locale } from "../lib/types";
import { draftStatus, localText } from "../lib/agentDraft";

const auditKinds: Record<string, [string, string]> = {
  initial: ["创建草稿", "Draft created"], create: ["创建草稿", "Draft created"], clone: ["复制现有Agent", "Agent copied"],
  manual_edit: ["保存表单修改", "Form changes saved"], undo: ["恢复历史修订", "Revision restored"], import: ["导入草稿", "Draft imported"],
};
const sourceLabels: Record<string, [string, string]> = { blank: ["空白模板", "Blank template"], clone: ["现有Agent", "Existing Agent"], free_query: ["自由查询结果", "Free-query result"], workflow_gap: ["工作流能力缺口", "Workflow capability gap"] };

export default function AgentDraftConversation({ turns, locale }: { turns: any[]; locale: Locale }) {
  let chatNumber = 0;
  const language = locale === "zh" ? 0 : 1;
  return <div className="draft-conversation-history">{turns.map((turn, index) => {
    const audit = auditKinds[turn.kind];
    const pending = ["queued", "running", "waiting_input"].includes(turn.status);
    const reply = localText(turn.assistant_message || turn.decision?.assistant_message || turn.decision?.summary, locale);
    const userMessage = turn.user_message || turn.feedback;
    const source = sourceLabels[turn.decision?.source]?.[language];
    if (!audit) chatNumber += 1;
    return <article key={turn.turn ?? turn.turn_number ?? index} className={audit ? "draft-audit-entry" : "draft-chat-entry"}>
      <h3>{audit ? audit[language] : locale === "zh" ? `第${chatNumber}轮对话` : `Conversation ${chatNumber}`}{!audit && <> · {draftStatus(turn.status || turn.decision?.action, locale)}</>}</h3>
      {audit ? <p>{source && <>{locale === "zh" ? "来源：" : "Source: "}{source} · </>}{reply || (locale === "zh" ? "此记录为草稿操作，不是待回复的对话。" : "This is a draft action, not a conversation awaiting reply.")}</p> : <>
        <p className="draft-user-message">{userMessage || (locale === "zh" ? "历史用户消息未记录" : "Historical user message was not recorded")}</p>
        <p>{reply || (pending ? locale === "zh" ? "正在处理，请稍候。" : "Processing. Please wait." : locale === "zh" ? "该轮已结束，未记录助手回复。" : "This turn ended without a recorded assistant reply.")}</p>
      </>}
      {turn.result_revision != null && <small>{locale === "zh" ? "已保存修订" : "Saved revision"} {turn.result_revision}</small>}
    </article>;
  })}</div>;
}
