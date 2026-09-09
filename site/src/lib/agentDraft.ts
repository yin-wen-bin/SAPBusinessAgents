import type { ExecutionInputProperty, ExecutionInputSchema, Locale } from "./types";

export const draftTerminal = new Set(["completed", "inconclusive", "failed", "cancelled", "expired", "ready", "needs_input", "timed_out", "unavailable", "interrupted"]);
export const draftStepNames = ["compose", "review", "publish"] as const;
export const localText = (value: any, locale: Locale): string => typeof value === "object" && value !== null ? String(value[locale] ?? "") : String(value ?? "");
export const publicInput = (property: ExecutionInputProperty) => property["x-sapba-internal"] !== true && property["x-sapba-workflow-only"] !== true;
export const inputType = (property: ExecutionInputProperty) => Array.isArray(property.type) ? property.type.find((value) => value !== "null") : property.type;
export const parseList = (value: string) => value.split(/[\n,;，；]+/u).map((item) => item.trim()).filter(Boolean);

export function technicalIdentity(draft: any) {
  const identity = draft?.technical_identity || {};
  const kind = ["new_agent", "version_upgrade"].includes(identity.kind) ? identity.kind : "unknown";
  const locked = identity.locked === true || kind !== "new_agent" || draft?.status === "published";
  return { ...identity, kind, agent_id: String(identity.agent_id || draft?.agent_id || ""), locked, confirmed: identity.confirmed === true && kind !== "unknown", can_rename: identity.can_rename === true && !locked, blockers: Array.isArray(identity.blockers) ? identity.blockers : [] };
}

export function technicalIdError(value: string, locale: Locale): string {
  if (value.length < 3 || value.length > 80 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value)) return locale === "zh" ? "使用3–80个小写字母、数字或单个连字符分段；不能含空格或首尾、连续连字符。" : "Use 3–80 lowercase letters or digits, with single hyphens between segments; no spaces, leading, trailing or repeated hyphens.";
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(value)) return locale === "zh" ? "该名称是系统保留名，请换一个技术 ID。" : "This is a reserved system name. Choose another technical ID.";
  return "";
}

const nonnegativeSeconds = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const timestamp = (value: unknown): number | null => typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
export function feedbackTiming(turn: any, now = Date.now()) {
  const execution = turn?.decision?.execution || {};
  const active = ["queued", "running", "waiting_input", "cancelling"].includes(turn?.status);
  const started = timestamp(execution.started_at);
  const deadline = timestamp(execution.deadline_at);
  const completed = timestamp(turn?.completed_at);
  const recorded = nonnegativeSeconds(execution.elapsed_seconds);
  const elapsed = active && started !== null ? Math.max(recorded ?? 0, (now - started) / 1000, 0) : recorded ?? (started !== null && completed !== null ? Math.max(0, (completed - started) / 1000) : null);
  const limit = nonnegativeSeconds(execution.timeout_seconds);
  return { active, elapsed_seconds: elapsed, timeout_seconds: limit !== null && limit > 0 ? limit : null, deadline_at: deadline === null ? null : execution.deadline_at as string, deadline_reached: active && deadline !== null && now >= deadline };
}

export function feedbackDuration(seconds: number | null, locale: Locale): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return locale === "zh" ? "未记录" : "Not recorded";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), remainder = total % 60;
  return locale === "zh" ? `${hours ? `${hours}小时` : ""}${hours || minutes ? `${minutes}分` : ""}${remainder}秒` : `${hours ? `${hours}h ` : ""}${hours || minutes ? `${minutes}m ` : ""}${remainder}s`;
}

export function feedbackFailureText(code: unknown, locale: Locale): string {
  const labels: Record<string, [string, string]> = {
    agent_feedback_timeout: ["本轮超过记录的处理时限，未应用修改。请核对当前修订后重试。", "This turn exceeded its recorded processing limit; no changes were applied. Review the current revision before retrying."],
    runtime_model_authentication_failed: ["Runtime 登录已失效或认证失败，请在系统配置中重新登录。", "Runtime authentication failed or expired. Sign in again in system settings."],
    runtime_model_incompatible: ["草稿绑定模型与当前 SDK 不兼容，请检查模型目录；不会自动切换模型。", "The draft model is incompatible with the installed SDK. Check the model catalog; no automatic fallback is used."],
    runtime_model_access_unavailable: ["当前账号无权使用草稿绑定模型，请核对账号与模型权限。", "The current account cannot access this draft's model. Check account and model permissions."],
    runtime_agent_feedback_connection_failed: ["Runtime 连接中断，请检查网络后重试。", "The Runtime connection was interrupted. Check your connection and retry."],
    runtime_agent_feedback_invalid: ["Runtime 返回的修改未通过响应校验，未应用修改；可缩小修改范围后重试。", "The Runtime response failed validation; no changes were applied. Narrow the requested changes and retry."],
    agent_definition_invalid: ["生成的 Agent 定义未通过平台校验，未应用修改。请根据下方原因调整修改意见后重试。", "The generated Agent definition failed platform validation; no changes were applied. Review the details below and revise your feedback before retrying."],
    agent_feedback_cancelled: ["本轮已取消。您可以核对原意见后重新发送。", "This turn was cancelled. You can review the original request and send it again."],
    agent_feedback_interrupted: ["本轮因服务中断而结束。请核对当前修订后重试。", "This turn ended after a service interruption. Review the current revision before retrying."],
    agent_feedback_cleanup_failed: ["本轮后台清理未完成，草稿仍被锁定。请重启 API 服务并确认清理完成后再重试。", "Background cleanup did not finish and the draft remains locked. Restart the API service and confirm cleanup before retrying."],
    agent_feedback_retry_invalid: ["原对话不符合重试条件，请核对历史记录后重新填写意见。", "The original turn cannot be retried. Review the history and enter a new request."],
    runtime_agent_feedback_connection_timeout: ["本轮 Runtime 连接超时，并非本轮总时限已用完。请检查连接后重试。", "The Runtime connection timed out; this does not mean the full turn time limit was reached. Check the connection before retrying."],
    agent_runtime_binding_missing: ["本轮缺少可用的模型绑定，请先检查系统中的 Runtime 配置。", "This turn has no available model binding. Check the system Runtime configuration first."],
    agent_runtime_snapshot_failed: ["平台读取本轮模型配置失败，尚未调用模型或应用修改。这不是推理强度未设置；请更新并重启 API 服务后重试。", "The platform could not load this turn's model configuration; no model call or changes were made. This is not an unset reasoning effort. Update and restart the API service before retrying."],
    runtime_agent_feedback_unavailable: ["当前 Runtime 不支持修改对话，请检查系统配置。", "The current Runtime does not support feedback conversations. Check system settings."],
    runtime_not_selectable: ["本轮使用的 Runtime 暂不可用，请检查系统配置后重试。", "The Runtime for this turn is unavailable. Check system settings before retrying."],
    runtime_model_check_required: ["本轮模型与推理强度尚未通过兼容检查，请在系统配置中完成检查。", "The model and reasoning effort require a compatibility check in system settings."],
    runtime_reasoning_effort_unsupported: ["本轮推理强度不受支持，请检查该模型的配置。", "The reasoning effort for this turn is unsupported. Check this model's settings."],
    runtime_reasoning_effort_invalid: ["本轮推理强度不在该模型的支持列表中，请重新选择并检查兼容性。", "This reasoning effort is not supported by the model. Select another value and check compatibility."],
    runtime_model_catalog_required: ["本轮模型目录尚不可用，请在系统配置中刷新目录并检查兼容性。", "The model catalog is unavailable. Refresh it in system settings and check compatibility."],
    runtime_model_not_registered: ["草稿绑定的模型不在当前目录中。请检查系统配置，不会自动切换模型。", "The draft's bound model is not in the current catalog. Check system settings; the model will not be switched automatically."],
    agent_draft_conflict: ["草稿修订已变化。请核对最新内容后重试。", "The draft revision changed. Review the latest content before retrying."],
    agent_draft_operation_active: ["另一项草稿操作尚未结束，请等待或取消后重试。", "Another draft operation is still active. Wait or cancel it before retrying."],
    agent_authoring_context_too_large: ["当前定义与对话内容过长，请缩小本轮修改范围后重试。", "The definition and conversation are too large. Narrow the requested change before retrying."],
  };
  return labels[String(code)]?.[locale === "zh" ? 0 : 1] || (locale === "zh" ? "本轮未完成。请核对当前修订和系统配置后重试。" : "This turn did not complete. Review the current revision and system settings before retrying.");
}

export function feedbackEffort(value: unknown, locale: Locale): string {
  if (value === undefined || value === null || value === "") return locale === "zh" ? "未记录" : "Not recorded";
  const labels: Record<string, [string, string]> = { none: ["无", "None"], minimal: ["最低", "Minimal"], low: ["低", "Low"], medium: ["中", "Medium"], high: ["高", "High"], xhigh: ["超高", "Extra high"], max: ["最高", "Maximum"], ultra: ["极高", "Ultra"] };
  return labels[String(value)] ? `${labels[String(value)][locale === "zh" ? 0 : 1]} (${value})` : String(value);
}

export function canRetryFeedback(turn: any): boolean {
  return turn?.kind === "feedback" && ["failed", "cancelled", "interrupted", "timed_out", "expired"].includes(turn.status) && Number.isInteger(turn.turn ?? turn.turn_number) && (turn.turn ?? turn.turn_number) > 0 && typeof (turn.user_message ?? turn.feedback) === "string" && Boolean((turn.user_message ?? turn.feedback).trim());
}

export type FeedbackRequest = { baseTurn: number; baseRevision: number; feedback: string; locale: Locale; retryOfTurn?: number; requestId: string };
/** An uncertain network response is a retransmission, not a new logical conversation. */
export function prepareFeedbackRequest(previous: FeedbackRequest | null, next: Omit<FeedbackRequest, "requestId">, createId: () => string): FeedbackRequest {
  if (previous && previous.feedback === next.feedback && previous.locale === next.locale && previous.retryOfTurn === next.retryOfTurn) return previous;
  return { ...next, requestId: createId() };
}
export function presentationCell(row: any, index: number, key: string): any {
  if (Array.isArray(row?.values)) return row.values[index];
  if (Array.isArray(row)) return row[index];
  return row?.[key];
}
const supplied = (value: any) => value !== undefined && value !== null && value !== "";

/** Display only platform-authored codes and structural paths, never error payloads. */
export function feedbackValidationIssues(turn: any, locale: Locale): { text: string; path: string }[] {
  const labels: Record<string, [string, string]> = {
    definition_invalid: ["Agent 定义不符合执行契约，请检查定义与静态检查结果。", "The Agent definition does not satisfy its execution contract. Review the definition and static checks."],
    input_schema_invalid: ["输入定义必须是包含字段列表的对象 Schema。", "Inputs must be an object Schema with a properties list."],
    json_schema_invalid: ["字段类型或约束不符合 JSON Schema 格式。", "A field type or constraint is not valid JSON Schema."],
    input_title_missing: ["此输入字段缺少中文或英文名称。", "This input field is missing a Chinese or English name."],
    input_title_zh_invalid: ["此输入字段的中文名称必须包含中文业务名称。", "This input field needs a Chinese business name in its Chinese title."],
    input_title_en_invalid: ["此输入字段的英文名称不能包含中文字符。", "This input field's English title must not contain Chinese characters."],
    input_display_mismatch: ["输入展示清单与输入字段名称不一致。", "The input display list does not match the input field titles."],
    output_display_mismatch: ["输出展示清单与输出字段名称不一致。", "The output display list does not match the output field titles."],
    execution_mode_invalid: ["固定 Agent 的执行模式必须为确定性执行。", "A fixed Agent must use deterministic execution."],
  };
  const issues = turn?.decision?.validation_issues;
  if (!Array.isArray(issues)) return [];
  return issues.slice(0, 20).map((issue) => ({
    text: (labels[issue?.code] || labels.definition_invalid)[locale === "zh" ? 0 : 1],
    path: typeof issue?.path === "string" && issue.path.length <= 512 && /^\/manifest(?:\/(?:[A-Za-z_][A-Za-z0-9_-]{0,79}|[0-9]+))*$/.test(issue.path) ? issue.path : "/manifest",
  }));
}

export function inputRequirement(schema: ExecutionInputSchema | ExecutionInputProperty, key: string, values: Record<string, any>, locale: Locale): string {
  const definition = schema as ExecutionInputSchema;
  const property = definition.properties?.[key];
  if (property?.["x-sapba-server-default"]) return locale === "zh" ? "可留空 · 服务端默认" : "May be empty · server default";
  const branch = publicBranchRequirements(definition, values);
  if (branch.required.includes(key)) return locale === "zh" ? "必填" : "Required";
  const conditional = ((branch.ambiguous || branch.unavailable) && definition.oneOf?.some((item) => item.required?.includes(key))) || Object.values(definition.dependentRequired || {}).some((names) => names.includes(key));
  if (conditional) return locale === "zh" ? "按条件必填" : "Conditionally required";
  return locale === "zh" ? "可选" : "Optional";
}

/** Read Schema titles, never the redundant manifest.inputs display list. */
export function draftInputLabels(schema: ExecutionInputSchema, locale: Locale, values: Record<string, any> = {}) {
  return Object.entries(schema.properties || {}).filter(([, property]) => property && typeof property === "object" && publicInput(property)).map(([key, property]) => ({
    key, label: localText(property.title, locale) || key, requirement: inputRequirement(schema, key, values, locale),
  }));
}

/** Resolve only branches reachable from public form input; never invent an implicit mode. */
export function publicBranchRequirements(schema: ExecutionInputSchema, values: Record<string, any> = {}): { required: string[]; ambiguous: boolean; unavailable: boolean } {
  const base = (schema.required || []).filter((name) => schema.properties?.[name] && publicInput(schema.properties[name]));
  if (!schema.oneOf?.length) return { required: base, ambiguous: false, unavailable: false };
  const viable = schema.oneOf.filter((branch) => {
    if ((branch.required || []).some((name) => !supplied(values[name]) && (!schema.properties?.[name] || !publicInput(schema.properties[name])))) return false;
    if (branch.not?.required?.every((name) => supplied(values[name]))) return false;
    return Object.entries(branch.properties || {}).every(([name, constraint]) => {
      if (!supplied(values[name])) return true;
      if (Object.prototype.hasOwnProperty.call(constraint, "const") && constraint.const !== values[name]) return false;
      if (constraint.enum && !constraint.enum.includes(values[name])) return false;
      return true;
    });
  });
  const matching = viable.filter((branch) => (branch.required || []).every((name) => supplied(values[name])));
  const selected = matching.length === 1 ? matching[0] : viable.length === 1 ? viable[0] : null;
  const required = selected?.required || (viable.length ? (viable[0].required || []).filter((name) => viable.every((branch) => branch.required?.includes(name))) : []);
  return { required: [...new Set([...base, ...required])].filter((name) => schema.properties?.[name] && publicInput(schema.properties[name])), ambiguous: !selected && viable.length > 1, unavailable: viable.length === 0 };
}

/** Defaults and discovery projections never carry sensitive values into ordinary input. */
export function publicValues(schema: ExecutionInputSchema | ExecutionInputProperty, supplied: Record<string, any> = {}, defaults = false): Record<string, any> {
  const result: Record<string, any> = {};
  Object.entries(schema.properties || {}).forEach(([key, property]) => {
    if (!publicInput(property) || property["x-sapba-sensitive"]) return;
    let value = supplied[key];
    if (value === undefined && defaults && !property["x-sapba-server-default"]) value = property.default;
    if (value === undefined || value === "") return;
    if (inputType(property) === "object") value = publicValues(property, value, defaults);
    if (inputType(property) === "array" && inputType(property.items || {} as ExecutionInputProperty) === "object") value = Array.isArray(value) ? value.map((item) => publicValues(property.items!, item, defaults)) : [];
    else if (inputType(property) === "array" && typeof value === "string") value = parseList(value);
    result[key] = value;
  });
  return result;
}

export function validateDraftInput(schema: ExecutionInputSchema, values: Record<string, any>, secrets: Record<string, string>, locale: Locale): Record<string, string> {
  const errors: Record<string, string> = {};
  const visit = (definition: ExecutionInputSchema | ExecutionInputProperty, input: Record<string, any>, prefix = "") => {
    const branch = publicBranchRequirements(definition as ExecutionInputSchema, input);
    Object.entries(definition.properties || {}).forEach(([key, property]) => {
      if (!publicInput(property)) return;
      const path = prefix ? `${prefix}.${key}` : key;
      const label = localText(property.title, locale) || key;
      const value = property["x-sapba-sensitive"] ? secrets[path] : input[key];
      const fail = (zh: string, en: string) => { errors[path] ||= `${label}${locale === "zh" ? zh : ` ${en}`}`; };
      if (value === undefined || value === null || value === "") {
        if (branch.required.includes(key) && !property["x-sapba-server-default"]) fail("为必填项。", "is required.");
        return;
      }
      const type = inputType(property);
      if (type === "array") {
        const items = Array.isArray(value) ? value : parseList(String(value));
        if (items.length < (property.minItems ?? 0)) fail(`至少需要${property.minItems}项。`, `needs at least ${property.minItems} items.`);
        if (property.maxItems !== undefined && items.length > property.maxItems) fail(`最多允许${property.maxItems}项。`, `allows at most ${property.maxItems} items.`);
        if (property.uniqueItems && new Set(items.map((item) => JSON.stringify(item))).size !== items.length) fail("不能包含重复项。", "cannot contain duplicates.");
        items.forEach((item, index) => {
          if (property.items?.type === "object") visit(property.items, item, `${path}.${index}`);
          else if (property.items?.pattern && !new RegExp(property.items.pattern).test(String(item))) fail("包含格式不正确的项目。", "contains an invalid item.");
        });
      } else if (type === "object") visit(property, value, path);
      else if (type === "number" || type === "integer") {
        const numeric = Number(value);
        if (!Number.isFinite(numeric) || (type === "integer" && !Number.isInteger(numeric))) fail("必须为有效数字。", "must be a valid number.");
        if (property.minimum !== undefined && numeric < property.minimum) fail(`不能小于${property.minimum}。`, `must be at least ${property.minimum}.`);
        if (property.maximum !== undefined && numeric > property.maximum) fail(`不能大于${property.maximum}。`, `must not exceed ${property.maximum}.`);
        if (property.exclusiveMinimum !== undefined && numeric <= property.exclusiveMinimum) fail(`必须大于${property.exclusiveMinimum}。`, `must exceed ${property.exclusiveMinimum}.`);
      } else if (type === "string") {
        if (property.minLength !== undefined && String(value).length < property.minLength) fail("长度不足。", "is too short.");
        if (property.maxLength !== undefined && String(value).length > property.maxLength) fail("长度超出限制。", "is too long.");
        if (property.pattern && !new RegExp(property.pattern).test(String(value))) fail("格式无效。", "has an invalid format.");
        if (property.format === "date" && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value)) fail("必须是有效日期。", "must be a valid date.");
      }
      if (property.enum && !property.enum.includes(value)) fail("不是允许的选项。", "is not an allowed option.");
    });
    if (branch.ambiguous || branch.unavailable) errors[prefix ? `${prefix}._schema` : "_schema"] = locale === "zh" ? "请补充查询方式对应的参数，当前输入尚不能确定唯一合法的查询方式。" : "Complete the parameters for a single supported query mode; this input does not yet select a unique mode.";
  };
  visit(schema, values);
  (schema.dateRangePairs || []).forEach((pair) => {
    if (!values[pair.from] || !values[pair.to]) return;
    const days = (Date.parse(values[pair.to]) - Date.parse(values[pair.from])) / 86400000;
    if (days < 0 || (pair.maxDays !== undefined && days > pair.maxDays)) errors[pair.to] = locale === "zh" ? "请检查日期先后顺序及允许的日期跨度。" : "Check date order and the allowed date range.";
  });
  (schema.numericOrderPairs || []).forEach((pair) => {
    if (values[pair.lower] !== undefined && values[pair.upper] !== undefined && Number(values[pair.lower]) > Number(values[pair.upper])) errors[pair.upper] = locale === "zh" ? "结束值不能小于起始值。" : "The upper value must not be below the lower value.";
  });
  Object.entries(schema.dependentRequired || {}).forEach(([key, required]) => {
    if (values[key] !== undefined && values[key] !== "") required.forEach((name) => { if (values[name] === undefined || values[name] === "") errors[name] = locale === "zh" ? "填写关联参数后，该项为必填项。" : "This field is required with the related input."; });
  });
  return errors;
}

export function discoveryFingerprint(revision: number, values: Record<string, any>): string {
  const canonical = (value: any): any => Array.isArray(value) ? value.map(canonical) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
  return JSON.stringify([revision, canonical(values)]);
}

/** Restoring a sample is never implicit approval to execute it. */
export function restoreDiscoveredInput(schema: ExecutionInputSchema, revision: number, current: Record<string, any>, sample: any, previousAutoFilled: Record<string, any> = {}) {
  if (sample?.revision !== revision || !["ready", "completed", "needs_input"].includes(sample?.status)) return null;
  const projected = publicValues(schema, sample.suggested_inputs || sample.input || {});
  const input = publicValues(schema, current);
  const autoFilled = Object.fromEntries(Object.entries(previousAutoFilled).filter(([key, value]) => discoveryFingerprint(0, { value: input[key] }) === discoveryFingerprint(0, { value })));
  const scopeChanged = Object.entries(input).some(([key, value]) => supplied(value) && discoveryFingerprint(0, { value }) !== discoveryFingerprint(0, { value: projected[key] }));
  if (scopeChanged) return { input, autoFilled, autoDiscover: true, confirmedSample: "", fingerprint: "", scopeChanged: true };
  Object.entries(projected).forEach(([key, value]) => {
    if (supplied(input[key])) return; // Never overwrite a user's scope or manual correction.
    input[key] = value;
    if (sample.field_sources?.[key]) autoFilled[key] = value;
  });
  return { input, autoFilled, autoDiscover: true, confirmedSample: "", fingerprint: discoveryFingerprint(revision, input), scopeChanged: false };
}

/** Switching to manual mode removes unedited discovered values, not user-provided scope. */
export function clearDiscoveredInput(input: Record<string, any>, autoFilled: Record<string, any>): Record<string, any> {
  const manual = { ...input };
  Object.entries(autoFilled).forEach(([key, value]) => {
    if (discoveryFingerprint(0, { value: manual[key] }) === discoveryFingerprint(0, { value })) delete manual[key];
  });
  return manual;
}

export function changedDefinition(draft: any, manifestText: string, readme: string, rules: string): boolean {
  try { return JSON.stringify(JSON.parse(manifestText)) !== JSON.stringify(draft.package.manifest) || readme !== (draft.package.readme || "") || rules !== (draft.package.rules || ""); }
  catch { return true; }
}

export function retainCompatibleInput(previousSchema: ExecutionInputSchema, nextSchema: ExecutionInputSchema, values: Record<string, any>): Record<string, any> {
  const compatible = Object.fromEntries(Object.entries(values).filter(([key, value]) => {
    const before = previousSchema.properties?.[key];
    const after = nextSchema.properties?.[key];
    return before && after && JSON.stringify(before.type) === JSON.stringify(after.type) && (!after.enum || after.enum.includes(value));
  }));
  return publicValues(nextSchema, compatible);
}

export function diffBusinessLabel(change: any, manifest: any, locale: Locale): string {
  const groups: Record<string, [string, string]> = { basic: ["基础信息", "Basic information"], inputs_outputs: ["输入与输出", "Inputs and outputs"], steps: ["业务处理步骤", "Business steps"], data_sources: ["SAP数据源", "SAP data sources"], rules: ["受控业务规则", "Managed business rules"], documentation: ["说明文档", "Documentation"] };
  const path = String(change.path || "");
  const key = path.split("/").filter(Boolean);
  const name = key[key.length - 1];
  const fields: Record<string, [string, string]> = { title: ["名称", "Name"], summary: ["说明", "Summary"], owner: ["负责人", "Owner"], version: ["目标版本", "Target version"], required: ["必填约束", "Required fields"], readme: ["使用说明", "README"], rules: ["规则源码", "Rule source"] };
  const field = fields[name] || fields[key[key.length - 2]];
  const inputIndex = key.indexOf("properties");
  const schema = path.includes("inputSchema") ? manifest.execution?.inputSchema : manifest.execution?.outputSchema;
  const input = inputIndex >= 0 ? schema?.properties?.[key[inputIndex + 1]] : null;
  const main = localText(change.label || change.title, locale) || (input ? localText(input.title, locale) : "") || field?.[locale === "zh" ? 0 : 1] || groups[change.group]?.[locale === "zh" ? 0 : 1] || (locale === "zh" ? "定义内容" : "Definition");
  return `${main}${name === "zh" ? locale === "zh" ? "（中文）" : " (Chinese)" : name === "en" ? locale === "zh" ? "（英文）" : " (English)" : ""}`;
}

export function draftStatus(value: unknown, locale: Locale): string {
  const labels: Record<string, [string, string]> = {
    draft: ["编辑中", "Editing"], queued: ["等待执行", "Queued"], running: ["执行中", "Running"], cancelling: ["正在取消并清理", "Cancelling and cleaning up"], finalizing: ["生成结果", "Finalizing"], waiting_input: ["等待补充", "Waiting for input"],
    received: ["已接收请求", "Request received"], preparing: ["准备查询", "Preparing query"], reading_sap: ["读取SAP证据", "Reading SAP evidence"], validating_evidence: ["核验证据", "Checking evidence"], preparing_result: ["生成业务结果", "Preparing business result"],
    completed: ["执行完成", "Completed"], interrupted: ["任务已中断", "Interrupted"], ready: ["样本已找到，请核对", "Sample ready for review"], needs_input: ["需要补充参数", "More input needed"], timed_out: ["任务超时", "Timed out"], unavailable: ["暂不可用", "Unavailable"], inconclusive: ["证据不足", "Inconclusive"], failed: ["执行失败", "Failed"], cancelled: ["已取消", "Cancelled"], expired: ["已过期", "Expired"],
    normal: ["正常", "Normal"], attention: ["需要处理", "Needs attention"], unknown: ["无法确认", "Unknown"], PASS: ["验收通过", "Passed"], FAIL: ["验收失败", "Failed"], BLOCKED: ["受阻", "Blocked"], NOT_TESTED: ["尚未验收", "Not tested"],
    metadata_only: ["仅文案修改", "Documentation only"], behavior_change: ["执行行为变化", "Behavior changed"], clarify: ["需要澄清", "Clarification needed"], answer: ["说明与回答", "Explanation"], revise: ["已修改草稿", "Draft revised"],
  };
  return labels[String(value)]?.[locale === "zh" ? 0 : 1] || (value ? (locale === "zh" ? "待确认" : "Pending review") : "—");
}
