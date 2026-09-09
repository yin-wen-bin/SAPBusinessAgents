import type { ExecutionInputProperty, ExecutionInputSchema, Locale } from "./types";

export const draftTerminal = new Set(["completed", "inconclusive", "failed", "cancelled", "expired", "ready", "needs_input", "timed_out", "unavailable", "interrupted"]);
export const draftStepNames = ["compose", "review", "publish"] as const;
export const localText = (value: any, locale: Locale): string => typeof value === "object" && value !== null ? String(value[locale] ?? "") : String(value ?? "");
export const publicInput = (property: ExecutionInputProperty) => property["x-sapba-internal"] !== true && property["x-sapba-workflow-only"] !== true;
export const inputType = (property: ExecutionInputProperty) => Array.isArray(property.type) ? property.type.find((value) => value !== "null") : property.type;
export const parseList = (value: string) => value.split(/[\n,;，；]+/u).map((item) => item.trim()).filter(Boolean);
export function presentationCell(row: any, index: number, key: string): any {
  if (Array.isArray(row?.values)) return row.values[index];
  if (Array.isArray(row)) return row[index];
  return row?.[key];
}
const supplied = (value: any) => value !== undefined && value !== null && value !== "";

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
    draft: ["编辑中", "Editing"], queued: ["等待执行", "Queued"], running: ["执行中", "Running"], finalizing: ["生成结果", "Finalizing"], waiting_input: ["等待补充", "Waiting for input"],
    received: ["已接收请求", "Request received"], preparing: ["准备查询", "Preparing query"], reading_sap: ["读取SAP证据", "Reading SAP evidence"], validating_evidence: ["核验证据", "Checking evidence"], preparing_result: ["生成业务结果", "Preparing business result"],
    completed: ["执行完成", "Completed"], interrupted: ["任务已中断", "Interrupted"], ready: ["样本已找到，请核对", "Sample ready for review"], needs_input: ["需要补充参数", "More input needed"], timed_out: ["任务超时", "Timed out"], unavailable: ["暂不可用", "Unavailable"], inconclusive: ["证据不足", "Inconclusive"], failed: ["执行失败", "Failed"], cancelled: ["已取消", "Cancelled"], expired: ["已过期", "Expired"],
    normal: ["正常", "Normal"], attention: ["需要处理", "Needs attention"], unknown: ["无法确认", "Unknown"], PASS: ["验收通过", "Passed"], FAIL: ["验收失败", "Failed"], BLOCKED: ["受阻", "Blocked"], NOT_TESTED: ["尚未验收", "Not tested"],
    metadata_only: ["仅文案修改", "Documentation only"], behavior_change: ["执行行为变化", "Behavior changed"], clarify: ["需要澄清", "Clarification needed"], answer: ["说明与回答", "Explanation"], revise: ["已修改草稿", "Draft revised"],
  };
  return labels[String(value)]?.[locale === "zh" ? 0 : 1] || (value ? (locale === "zh" ? "待确认" : "Pending review") : "—");
}
