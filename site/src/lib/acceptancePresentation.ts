// Presentation only: never grants acceptance, execution or publication authority.
import type { Locale } from "./types";
export const acceptanceErrorLabels: Record<string, [string, string]> = {
    source_scope_definition_required: ["请先明确并保存来源范围、记录粒度和类别口径；不能通过换源或删行取得验收通过。", "Save a reviewed source scope, record grain and category definition first. Do not switch sources or drop rows to obtain PASS."],
    source_detail_grain_incomplete: ["查询缺少真实行标识或分类维度，不能把汇总当作业务明细。", "The query omits true line identifiers or classification dimensions; an aggregate cannot be treated as business detail."],
    source_query_contract_rejected: ["查询来源的粒度或金额币种契约未通过，尚未读取业务数据。", "Source grain or amount/currency validation failed before business data was read."],
    source_category_coverage_gap: ["来源不覆盖本次要求的全部项目类别，不能据此断言不存在相关项目。", "The source does not cover all required item categories; absence cannot be concluded."],
    source_view_not_comparable: ["两侧使用的业务视图不可直接比较，请先核对范围与粒度。", "The business views are not directly comparable. Review scope and grain first."],
    source_configuration_applicability_missing: ["缺少适用于该过账时间的配置证明，相关项目保留为待核实。", "Configuration applicability at the posting date is unproven; the items remain unclassified."],
    source_non_additive_metric_rejected: ["多个账本的金额不能无声明相加，请保留分账本结果。", "Amounts from multiple ledgers cannot be silently added. Keep ledger-separated results."],
    source_projection_grain_mismatch: ["报告记录与取证粒度不一致，不能省略或合并原始记录。", "Report records do not match the evidence grain; source rows cannot be silently dropped or collapsed."],
    source_semantics_binding_changed: ["本次任务冻结的来源规则已变化，已阻止继续读取或保存迟到报告。", "Frozen source rules changed; further reads and late reports were blocked."],
    runtime_structured_output_missing: ["SDK未交接最终结构化结果；已保留SAP证据和报告校验记录，但不能据此判定验收通过。", "The SDK did not hand off a final structured result. SAP evidence and report validation were preserved, but do not establish acceptance PASS."],
    runtime_structured_output_invalid: ["模型最终结构化结果不符合终态契约，未生成验收结论。请查看具体格式原因。", "The model's final structured result violates the terminal contract. No acceptance verdict was issued; review the format errors."],
    runtime_terminal_correction_stalled: ["最终结构化结果多次纠错仍未收敛，已停止并保留证据；不会自动重试。", "Final structured-output correction stalled. Execution stopped and evidence was preserved; no automatic retry."],
    runtime_scope_clarification_required: ["查询范围未能绑定，请核对下方具体参数及原因。不会自动重试。", "The query scope could not be bound. Review the parameters and reasons below. No automatic retry."],
    runtime_scope_input_invalid: ["查询范围输入无法解析，请先核对范围契约。", "The query-scope inputs could not be parsed. Review the scope contract."],
    runtime_query_scope_rejected: ["查询未能证明符合已确认范围，已阻止读取。请核对具体范围原因。", "The query did not prove the confirmed scope; reading was blocked. Review the scope details."],
    runtime_skill_scope_rejected: ["读取工具未能证明符合已确认范围，已阻止读取。", "The read tool did not prove the confirmed scope; reading was blocked."],
    runtime_authentication_binding_invalid: ["运行绑定不完整，验收未能启动。请重新读取运行环境；无需更换业务案例。", "The runtime binding is incomplete; acceptance could not start. Read runtime information again; changing cases is unnecessary."],
    runtime_authentication_binding_changed: ["本次运行的认证绑定已变化，验收已阻断。请核对当前认证和运行环境，不代表账号已退出。", "The authentication binding changed; acceptance is blocked. Verify current authentication and runtime; this does not mean the account signed out."],
    workbuddy_service_site_changed: ["运行站点绑定已变化，请重新核对运行环境。", "The service-site binding changed. Verify the runtime again."],
    agent_acceptance_cancelled: ["验收已由用户取消，未生成发布证书。", "Acceptance was cancelled; no publication certificate was created."],
    agent_acceptance_interrupted: ["服务在验收期间重启或中断，请重新验收。", "The service restarted or stopped during acceptance. Run acceptance again."],
    agent_acceptance_queue_timeout: ["任务排队时间过长，尚未完成验收。", "The campaign remained queued too long and was not tested."],
    agent_acceptance_stage_timeout: ["当前验收阶段超时，未生成发布证书。", "The current acceptance stage timed out; no publication certificate was created."],
    agent_acceptance_stage_interrupted: ["当前验收阶段中断，尚未完成业务比较。", "The acceptance stage was interrupted before business comparison completed."],
    agent_acceptance_business_input_required: ["独立基线仍需确认业务信息，验收未完成。", "The independent baseline still needs business input; acceptance is incomplete."],
    independent_baseline_gap_unverified: ["基线未完成取证，所声明的缺口尚未得到可靠证据确认。", "Baseline investigation is incomplete; the stated gap has not been verified by reliable evidence."],
    agent_acceptance_internal_error: ["验收服务发生内部错误，请核对服务状态后重试。", "The acceptance service failed internally. Check service health and retry."],
    agent_acceptance_dependency_unavailable: ["平台验收组件未能加载。请先修复服务，再重新发起验收；无需更换业务案例。", "A platform acceptance component could not load. Repair the service before starting acceptance again; changing business cases is not required."],
    agent_acceptance_mode_invalid: ["当前Agent的验收模式无效，请选择三级验收或确定性运行验收。", "The Agent acceptance mode is invalid. Select three-stage or deterministic-runtime acceptance."],
    runtime_model_authentication_failed: ["Runtime认证失败，请在系统配置中重新登录。", "Runtime authentication failed. Sign in again in system settings."],
    runtime_model_check_required: ["模型与推理强度尚未通过兼容检查。", "The model and reasoning effort have not passed compatibility checking."],
  };

// Compatibility presentation only: never rewrite an old Campaign report.
export function acceptanceFailureCategory(report: any): string {
  const code = report?.error?.code;
  if (code === "agent_acceptance_business_input_required") return "input";
  if (code === "runtime_scope_clarification_required") {
    const issues = Array.isArray(report?.validation_issues) ? report.validation_issues : [];
    return issues.length && !issues.some((i: any) => i?.code === "runtime_scope_mapping_unsupported") ? "input" : "scope";
  }
  if (["runtime_scope_input_invalid", "runtime_query_scope_rejected", "runtime_skill_scope_rejected"].includes(code)) return "scope";
  return report?.failure_category || "";
}

export const acceptanceTerminal = new Set(["passed", "failed", "blocked", "cancelled", "interrupted", "superseded"]);

export function acceptanceAttempt(campaign: any, locale: Locale) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  if (!campaign) return { title: tr("尚未发起正式验收", "No formal acceptance attempt"), tone: "neutral", preparingFailed: false };
  const terminal = acceptanceTerminal.has(campaign.status);
  const cases = [...(campaign.cases || []), ...(campaign.report?.cases || [])];
  const started = cases.some(item => item.baseline_run_id || item.free_query_run_id || item.fixed_agent_run_id
    || [item.baseline, item.free_query, item.fixed_agent].some(stage => stage?.run_id || stage?.result_hash));
  const preparingFailed = terminal && !["cancelled", "superseded", "passed"].includes(campaign.status)
    && campaign.report?.verdict === "NOT_TESTED" && campaign.report?.failure_stage === "preparing" && !started;
  if (preparingFailed) return { title: tr("准备失败，业务验收未开始", "Preparation failed; business acceptance not started"), tone: "warning", preparingFailed };
  const titles: Record<string, [string, string]> = {
    queued: ["验收排队中", "Acceptance queued"], running: ["正在进行正式验收", "Formal acceptance in progress"],
    cancelling: ["正在停止并清理", "Stopping and cleaning up"], passed: ["本次验收通过", "This attempt passed"],
    failed: ["本次验收未通过", "This attempt failed"], blocked: ["本次验收受阻", "This attempt is blocked"],
    cancelled: ["本次验收已取消", "This attempt was cancelled"], interrupted: ["本次验收中断", "This attempt was interrupted"],
    superseded: ["此结果属于旧修订", "This result belongs to an earlier revision"],
  };
  return { title: (titles[campaign.status] || ["验收状态待核对", "Acceptance status needs verification"])[locale === "zh" ? 0 : 1],
    tone: campaign.status === "passed" ? "success" : terminal ? "warning" : "neutral", preparingFailed };
}

export function acceptanceBlockerText(item: any, locale: Locale): string {
  const message = item?.message || item?.description;
  const localized = typeof message === "string" ? message : message?.[locale] || message?.zh || message?.en;
  if (localized) return localized;
  const code = typeof item === "string" ? item : item?.code;
  const labels: Record<string, [string, string]> = {
    acceptance_required: ["当前修订尚需完成正式验收。", "Formal acceptance is required for this revision."],
    agent_acceptance_required: ["当前修订尚需完成正式验收。", "Formal acceptance is required for this revision."],
    agent_acceptance_dependency_unavailable: ["平台验收组件未能加载。请先修复服务，再重新设置验收；无需更换业务案例。", "An acceptance component could not load. Repair the service before setting up acceptance again; changing business cases is not required."],
    agent_acceptance_internal_error: ["验收服务发生内部错误，请先核对执行环境。", "The acceptance service failed internally. Check the execution environment first."],
    runtime_model_authentication_failed: ["Runtime认证未通过，请在系统配置中核对认证。", "Runtime authentication failed. Review authentication in system settings."],
    runtime_model_check_required: ["当前模型缺少适用的兼容检查结果。", "The model lacks an applicable compatibility check."],
  };
  return (labels[code] || acceptanceErrorLabels[code])?.[locale === "zh" ? 0 : 1]
    || (locale === "zh" ? "尚有条件需要核对，请查看技术诊断。" : "A requirement needs verification. Review technical diagnostics.");
}
