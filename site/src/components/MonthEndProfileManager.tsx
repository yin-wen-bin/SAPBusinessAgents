import { useEffect, useMemo, useState } from "react";

type Locale = "zh" | "en";
type Props = {
  apiBase: string;
  locale: Locale;
  validationVerdict: string;
  executable: boolean;
};
type Json = Record<string, any>;

const EVIDENCE_SOURCES = [
  "embedded_gl_line_items",
  "embedded_operational_due_items",
  "embedded_grir_chain",
  "embedded_billing_documents",
  "adt_asset_depreciation_status",
  "adt_fi_period_control",
  "adt_mm_period_status",
];

const CHECKS = [
  "AP_OVERDUE_ITEMS",
  "AR_UNAPPLIED_RECEIPTS",
  "GL_UNRECONCILED_ITEMS",
  "MM_GRIR_AGED_ITEMS",
  "MM_GRIR_ADJUSTMENTS_PENDING",
  "AA_DEPRECIATION_PENDING",
  "GL_FX_VALUATION_PENDING",
  "GL_AUTO_CLEARING_PENDING",
  "GL_PERIOD_CONTROL_ISSUE",
  "MM_PERIOD_CLOSE_PENDING",
  "CO_UNALLOCATED_COSTS",
  "SD_BILLING_TRANSFER_ERRORS",
];

const copy = {
  zh: {
    eyebrow: "公司月结配置", title: "管理本机公司月结配置", lead: "以结构化表单维护 profiles.json。保存后运行时立即读取，但不会改变月结 Agent 的实机验收状态。",
    add: "新增配置", refresh: "刷新", loading: "正在读取公司配置…", loadError: "无法读取月结配置服务。", empty: "尚未创建公司月结配置。",
    registryInvalid: "现有 profiles.json 无效。为保护原文件，前台已停止写入。", external: "当前使用外部 profiles.json，前台仅可查看。",
    company: "公司", system: "系统", client: "Client", version: "版本", effective: "生效期", ledger: "分类账", currency: "币种", fiscalVariant: "会计年度变式", leadingLedger: "Leading ledger", enabled: "已启用", disabled: "未启用",
    validated: "SAP 已验证", notValidated: "未完成 SAP 验证", edit: "编辑", duplicate: "复制", validateOnline: "在线校验", enable: "启用", disable: "停用",
    readyTitle: "公司配置状态", readyBody: "已启用且在线验证通过的配置可供运行表单选择。", gateTitle: "Agent 验收状态", gateReadyBody: "月结助手已通过三阶段真机验收，可以使用已启用配置运行。公司配置变更不会改变 Agent 验收状态。", gateBlockedBody: "月结助手尚未通过三阶段真机验收；配置成功不会解除执行门禁。",
    editorNew: "新增公司配置", editorEdit: "编辑公司配置", editorCopy: "复制公司配置", cancel: "取消", staticValidate: "检查配置", review: "检查摘要", back: "返回修改", save: "确认并保存", saving: "正在保存…",
    offlineNotice: "SAP 不可达时仍可保存，但配置保持未启用。只有当前内容完成在线元数据校验后才能启用。",
    groupIdentity: "1. 配置标识与生效范围", profileId: "Profile ID", effectiveFrom: "生效开始", effectiveTo: "生效结束（可选）",
    groupCompany: "2. 公司代码与分类账", companyCode: "公司代码", ledgerOptional: "分类账（留空时使用唯一 leading ledger）",
    groupAccounts: "3. 月结科目范围", grir: "GR/IR 科目", openItem: "未清项科目", autoClearing: "自动清账科目", bank: "银行科目", listHelp: "使用逗号、分号或换行分隔。",
    groupDocuments: "4. 收付款与评估凭证类型", incoming: "收款凭证类型", outgoing: "付款凭证类型", depreciation: "折旧凭证类型", fx: "外币评估凭证类型",
    groupThresholds: "5. GR/IR 与清账阈值", age: "GR/IR 账龄天数", highAge: "高严重度天数", qtyTolerance: "数量容差", amountTolerance: "金额容差", clearingTolerance: "自动清账容差",
    groupCo: "6. CO 范围", controllingArea: "控制范围", costCenters: "成本中心", internalOrders: "内部订单",
    groupStatuses: "7. 开票异常状态映射", transferErrors: "传输异常状态", postingErrors: "过账异常状态",
    groupEvidence: "8. 允许的只读补证来源", evidenceHelp: "这里只允许白名单中的 Embedded OData 与 ADT 只读来源。",
    groupOwners: "9. 12 项检查责任覆盖", ownerHelp: "只填写需要覆盖默认责任信息的检查。空白行不会写入配置。", department: "责任部门", owner: "责任人", remediation: "处理建议",
    groupPeriods: "10. 非 K4 / 特殊期间边界", periodHelp: "键格式为 YYYY-01 至 YYYY-16。K4 普通期间可留空。", periodKey: "期间", periodStart: "开始日期", periodEnd: "结束日期", addPeriod: "添加期间", remove: "移除",
    confirmation: "我确认科目、阈值、凭证类型、状态映射与责任信息已由业务团队审核。", summaryTitle: "保存前完整摘要", summaryHint: "请检查以下配置。新建配置固定从 1.0.0 开始，修改会自动提升补丁版本；业务内容变化会自动停用并要求重新在线验证。",
    success: "配置已保存。", validatedSuccess: "在线 SAP 元数据校验通过。", unavailable: "SAP 当前不可用；配置仍可保存为未启用。", enableConfirm: "确认启用这份已验证配置？启用后月结运行可以选择它。", disableConfirm: "确认停用这份配置？引用它的显式运行将返回配置已停用。",
    errors: "请修正以下字段：", conflict: "配置已被其他页面修改，请刷新后重试。", noDigest: "未能取得配置摘要，请刷新。", yes: "是", no: "否",
  },
  en: {
    eyebrow: "Company month-end profiles", title: "Manage local company closing profiles", lead: "Maintain profiles.json with a structured form. The runtime reads changes immediately, but the Agent acceptance gate is unchanged.",
    add: "Add profile", refresh: "Refresh", loading: "Loading company profiles…", loadError: "Could not reach the month-end profile service.", empty: "No company month-end profiles exist yet.",
    registryInvalid: "The existing profiles.json is invalid. UI writes are blocked to protect the file.", external: "An external profiles.json is active and is read-only in this UI.",
    company: "Company", system: "System", client: "Client", version: "Version", effective: "Effective dates", ledger: "Ledger", currency: "Currency", fiscalVariant: "Fiscal year variant", leadingLedger: "Leading ledger", enabled: "Enabled", disabled: "Disabled",
    validated: "SAP validated", notValidated: "Not SAP validated", edit: "Edit", duplicate: "Copy", validateOnline: "Validate online", enable: "Enable", disable: "Disable",
    readyTitle: "Company configuration", readyBody: "Enabled profiles with current online validation are offered in the run form.", gateTitle: "Agent acceptance", gateReadyBody: "The month-end Agent passed three-stage live acceptance and can run with an enabled profile. Profile changes do not alter the Agent acceptance state.", gateBlockedBody: "The month-end Agent has not passed three-stage live acceptance. Configuration does not release its execution gate.",
    editorNew: "Add company profile", editorEdit: "Edit company profile", editorCopy: "Copy company profile", cancel: "Cancel", staticValidate: "Check profile", review: "Review summary", back: "Back to edit", save: "Confirm and save", saving: "Saving…",
    offlineNotice: "If SAP is unavailable, the profile can still be saved but remains disabled. The current content must pass online metadata validation before enablement.",
    groupIdentity: "1. Identity and effective range", profileId: "Profile ID", effectiveFrom: "Effective from", effectiveTo: "Effective to (optional)",
    groupCompany: "2. Company code and ledger", companyCode: "Company code", ledgerOptional: "Ledger (leave empty for the unique leading ledger)",
    groupAccounts: "3. Closing account scope", grir: "GR/IR accounts", openItem: "Open-item accounts", autoClearing: "Auto-clearing accounts", bank: "Bank accounts", listHelp: "Separate values with commas, semicolons, or new lines.",
    groupDocuments: "4. Payment and valuation document types", incoming: "Incoming payments", outgoing: "Outgoing payments", depreciation: "Depreciation", fx: "FX valuation",
    groupThresholds: "5. GR/IR and clearing thresholds", age: "GR/IR age days", highAge: "High-severity days", qtyTolerance: "Quantity tolerance", amountTolerance: "Amount tolerance", clearingTolerance: "Auto-clearing tolerance",
    groupCo: "6. CO scope", controllingArea: "Controlling area", costCenters: "Cost centers", internalOrders: "Internal orders",
    groupStatuses: "7. Billing error mappings", transferErrors: "Transfer error statuses", postingErrors: "Posting error statuses",
    groupEvidence: "8. Approved read-only evidence sources", evidenceHelp: "Only allowlisted Embedded OData and ADT read-only sources can be selected.",
    groupOwners: "9. Owner overrides for twelve checks", ownerHelp: "Only fill checks that need to override the defaults. Empty rows are omitted.", department: "Department", owner: "Owner", remediation: "Remediation",
    groupPeriods: "10. Non-K4 / special-period boundaries", periodHelp: "Use keys YYYY-01 through YYYY-16. Normal K4 periods can remain empty.", periodKey: "Period", periodStart: "Start date", periodEnd: "End date", addPeriod: "Add period", remove: "Remove",
    confirmation: "I confirm that accounts, thresholds, document types, status mappings, and ownership were reviewed by the business team.", summaryTitle: "Complete pre-save summary", summaryHint: "Review the profile below. New profiles start at 1.0.0; edits auto-increment the patch version. Business changes disable the profile and require fresh online validation.",
    success: "Profile saved.", validatedSuccess: "Online SAP metadata validation passed.", unavailable: "SAP is unavailable; the profile can still be saved disabled.", enableConfirm: "Enable this validated profile? It will become selectable for month-end runs.", disableConfirm: "Disable this profile? Explicit runs that reference it will report that it is disabled.",
    errors: "Correct these fields:", conflict: "The profile changed in another page. Refresh and try again.", noDigest: "No registry digest is available. Refresh the page.", yes: "Yes", no: "No",
  },
};

function splitList(value: string): string[] {
  return Array.from(new Set(value.split(/[\n,;，；]+/u).map((item) => item.trim()).filter(Boolean)));
}

function joinList(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : "";
}

function cleanProfile(value: Json): Json {
  const profile = structuredClone(value);
  if (!profile.effective_to) delete profile.effective_to;
  if (!profile.ledger) delete profile.ledger;
  const owners: Json = {};
  for (const [check, owner] of Object.entries(profile.owners || {})) {
    const compact: Json = {};
    for (const key of ["department", "owner", "remediation"]) {
      const text = String((owner as Json)?.[key] || "").trim();
      if (text) compact[key] = text;
    }
    if (Object.keys(compact).length) owners[check] = compact;
  }
  profile.owners = owners;
  const boundaries: Json = {};
  for (const [key, range] of Object.entries(profile.period_boundaries || {})) {
    const name = String(key || "").trim();
    const start = String((range as Json)?.start || "").trim();
    const end = String((range as Json)?.end || "").trim();
    if (name || start || end) boundaries[name] = { start, end };
  }
  if (Object.keys(boundaries).length) profile.period_boundaries = boundaries;
  else delete profile.period_boundaries;
  return profile;
}

function emptyProfile(scope: Json): Json {
  return {
    profile_id: "", version: "1.0.0", enabled: false,
    system_alias: scope.system_alias || "default", sap_client: scope.sap_client || "",
    company_code: "", effective_from: new Date().toISOString().slice(0, 10), effective_to: "", ledger: "",
    accounts: { gr_ir: [], open_item_gl: [], auto_clearing: [], bank: [] },
    document_types: { incoming_payments: [], outgoing_payments: [], depreciation: [], fx_valuation: [] },
    thresholds: { grir_age_days: 90, grir_high_severity_days: 180, grir_quantity_tolerance: "0.001", grir_amount_tolerance: "0.01", auto_clearing_tolerance: "0.01" },
    co: { controlling_area: "", cost_centers: [], internal_orders: [] },
    status_mappings: { billing_transfer_errors: [], billing_posting_errors: [] },
    approved_evidence_sources: [...EVIDENCE_SOURCES], owners: {}, period_boundaries: {},
  };
}

async function request(url: string, init?: RequestInit): Promise<Json> {
  const response = await fetch(url, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const envelope = body?.detail || {};
    const error: any = new Error(envelope?.message || `HTTP ${response.status}`);
    error.code = envelope?.code;
    error.errors = envelope?.detail?.errors || [];
    throw error;
  }
  return body;
}

function ListField({ label, value, onChange, help }: { label: string; value: unknown; onChange: (values: string[]) => void; help?: string }) {
  return <label><span>{label}</span><textarea rows={2} value={joinList(value)} onChange={(event) => onChange(splitList(event.target.value))} />{help && <small>{help}</small>}</label>;
}

export default function MonthEndProfileManager({ apiBase, locale, validationVerdict, executable }: Props) {
  const t = copy[locale];
  const [registry, setRegistry] = useState<Json | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Json[]>([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Json | null>(null);
  const [editing, setEditing] = useState<Json | null>(null);
  const [editorMode, setEditorMode] = useState<"new" | "edit" | "copy">("new");
  const [reviewing, setReviewing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  const load = async () => {
    setLoading(true); setError("");
    try {
      const value = await request(`${apiBase}/api/month-end/profiles`);
      setRegistry(value);
      window.dispatchEvent(new CustomEvent("month-end-profiles-changed", { detail: value }));
    }
    catch { setError(t.loadError); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [apiBase]);

  const scope = registry?.runtime_scope || {};
  const profileEntries = registry?.profiles || [];
  const readyCount = profileEntries.filter((item: Json) => item.enabled && item.validation?.status === "validated").length;
  const title = editorMode === "edit" ? t.editorEdit : editorMode === "copy" ? t.editorCopy : t.editorNew;

  const update = (path: string, value: any) => setDraft((current) => {
    const next = structuredClone(current || {});
    const parts = path.split(".");
    let target = next;
    parts.slice(0, -1).forEach((part) => { target[part] ||= {}; target = target[part]; });
    target[parts.at(-1)!] = value;
    return next;
  });

  const openNew = () => {
    setDraft(emptyProfile(scope)); setEditing(null); setEditorMode("new"); setReviewing(false); setConfirmed(false); setFieldErrors([]); setNotice("");
  };
  const openEdit = (entry: Json) => {
    setDraft(structuredClone(entry.profile)); setEditing(entry); setEditorMode("edit"); setReviewing(false); setConfirmed(false); setFieldErrors([]); setNotice("");
  };
  const openCopy = (entry: Json) => {
    const next = structuredClone(entry.profile); next.profile_id = ""; next.version = "1.0.0"; next.enabled = false;
    setDraft(next); setEditing(null); setEditorMode("copy"); setReviewing(false); setConfirmed(false); setFieldErrors([]); setNotice("");
  };
  const closeEditor = () => { setDraft(null); setEditing(null); setReviewing(false); setConfirmed(false); setFieldErrors([]); };

  const validate = async (profile: Json, online: boolean) => {
    setBusy(true); setFieldErrors([]); setError(""); setNotice("");
    try {
      const result = await request(`${apiBase}/api/month-end/profiles/validate`, { method: "POST", body: JSON.stringify({ profile: cleanProfile(profile), online }) });
      const errors = [...(result.errors || []), ...(result.online_validation?.errors || [])].filter(
        (item, index, values) => values.findIndex((other) => other.field === item.field && other.code === item.code && other.message === item.message) === index,
      );
      setFieldErrors(errors);
      if (!errors.length && result.online_validation?.status === "validated") setNotice(t.validatedSuccess);
      else if (!errors.length && result.online_validation?.status === "unavailable") setNotice(t.unavailable);
      if (online) await load();
      return result.valid && errors.length === 0;
    } catch (caught: any) {
      setFieldErrors(caught.errors || []); setError(caught.message || t.loadError); return false;
    } finally { setBusy(false); }
  };

  const prepareReview = async () => {
    if (!draft) return;
    if (await validate(draft, false)) { setReviewing(true); setConfirmed(false); }
  };

  const save = async () => {
    if (!draft || !confirmed) return;
    if (!registry?.registry_digest) { setError(t.noDigest); return; }
    setBusy(true); setError(""); setFieldErrors([]);
    try {
      const body: Json = { profile: cleanProfile(draft), expected_registry_digest: registry.registry_digest };
      let path = "/api/month-end/profiles"; let method = "POST";
      if (editorMode === "edit" && editing) {
        path += `/${encodeURIComponent(editing.profile_id)}`; method = "PUT"; body.expected_profile_digest = editing.profile_digest;
      }
      await request(`${apiBase}${path}`, { method, body: JSON.stringify(body) });
      closeEditor(); setNotice(t.success); await load();
    } catch (caught: any) {
      setFieldErrors(caught.errors || []); setError(caught.code === "month_end_profile_conflict" ? t.conflict : caught.message || t.loadError);
    } finally { setBusy(false); }
  };

  const toggle = async (entry: Json) => {
    const enabled = !entry.enabled;
    if (!window.confirm(enabled ? t.enableConfirm : t.disableConfirm)) return;
    setBusy(true); setError(""); setFieldErrors([]);
    try {
      await request(`${apiBase}/api/month-end/profiles/${encodeURIComponent(entry.profile_id)}/enabled`, {
        method: "PUT",
        headers: enabled ? { "X-SAPBA-Action": "month-end-profile-enable" } : {},
        body: JSON.stringify({ enabled, expected_registry_digest: registry?.registry_digest, expected_profile_digest: entry.profile_digest }),
      });
      setNotice(t.success); await load();
    } catch (caught: any) {
      setFieldErrors(caught.errors || []); setError(caught.code === "month_end_profile_conflict" ? t.conflict : caught.message || t.loadError);
    } finally { setBusy(false); }
  };

  const periodRows = useMemo(() => Object.entries(draft?.period_boundaries || {}), [draft]);
  const replacePeriods = (rows: Array<[string, Json]>) => update("period_boundaries", Object.fromEntries(rows));

  return <section className="month-end-profile-manager" data-month-end-profile-manager>
    <header className="month-end-profile-header">
      <div><p className="runtime-eyebrow">{t.eyebrow}</p><h2>{t.title}</h2><p>{t.lead}</p></div>
      <div className="month-end-profile-actions"><button type="button" className="secondary-button" onClick={() => void load()} disabled={busy}>{t.refresh}</button><button type="button" className="primary-button" onClick={openNew} disabled={!registry?.editable || busy}>{t.add}</button></div>
    </header>
    <div className="month-end-readiness-grid">
      <article><strong>{t.readyTitle}</strong><b>{readyCount}</b><p>{t.readyBody}</p></article>
      <article className={executable ? "" : "is-gated"}><strong>{t.gateTitle}</strong><b>{validationVerdict}</b><p>{executable ? t.gateReadyBody : t.gateBlockedBody}</p></article>
    </div>
    {loading && <div className="month-end-notice">{t.loading}</div>}
    {registry && !registry.registry_valid && <div className="month-end-notice is-error"><strong>{t.registryInvalid}</strong><ul>{(registry.errors || []).map((item: Json, index: number) => <li key={index}>{item.field}: {item.message}</li>)}</ul></div>}
    {registry && registry.read_only_code && <div className="month-end-notice is-warning">{t.external}</div>}
    {error && <div className="month-end-notice is-error" role="alert">{error}</div>}
    {notice && <div className="month-end-notice is-success" role="status">{notice}</div>}
    {fieldErrors.length > 0 && <div className="month-end-notice is-error"><strong>{t.errors}</strong><ul>{fieldErrors.map((item, index) => <li key={`${item.field}-${index}`}><code>{item.field}</code> {item.message || item.code}</li>)}</ul></div>}

    {!loading && registry?.registry_valid && profileEntries.length === 0 && <div className="month-end-empty">{t.empty}</div>}
    {profileEntries.length > 0 && <div className="month-end-profile-list">{profileEntries.map((entry: Json) => <article key={entry.profile_id} className="month-end-profile-card">
      <header><div><h3>{entry.profile_id}</h3><p>{t.company} {entry.company_code} · {entry.system_alias} / {entry.sap_client || "—"}</p></div><div className="month-end-profile-badges"><span className={entry.enabled ? "is-enabled" : ""}>{entry.enabled ? t.enabled : t.disabled}</span><span className={entry.validation?.status === "validated" ? "is-validated" : ""}>{entry.validation?.status === "validated" ? t.validated : t.notValidated}</span></div></header>
      <dl><div><dt>{t.version}</dt><dd>{entry.version}</dd></div><div><dt>{t.effective}</dt><dd>{entry.effective_from} — {entry.effective_to || "∞"}</dd></div><div><dt>{t.ledger}</dt><dd>{entry.ledger || "leading"}</dd></div><div><dt>{t.currency} / {t.fiscalVariant}</dt><dd>{entry.validation?.metadata?.currency || "—"} / {entry.validation?.metadata?.fiscal_year_variant || "—"}</dd></div><div><dt>{t.leadingLedger}</dt><dd>{entry.validation?.metadata?.leading_ledger || "—"}</dd></div><div><dt>Digest</dt><dd><code>{String(entry.profile_digest).slice(0, 22)}…</code></dd></div></dl>
      <footer><button type="button" onClick={() => openEdit(entry)} disabled={!registry?.editable || busy}>{t.edit}</button><button type="button" onClick={() => openCopy(entry)} disabled={!registry?.editable || busy}>{t.duplicate}</button><button type="button" onClick={() => void validate(entry.profile, true)} disabled={busy}>{t.validateOnline}</button><button type="button" onClick={() => void toggle(entry)} disabled={!registry?.editable || busy || (!entry.enabled && entry.validation?.status !== "validated")}>{entry.enabled ? t.disable : t.enable}</button></footer>
    </article>)}</div>}

    {draft && <section className="month-end-editor" aria-label={title}>
      <header><div><h3>{title}</h3><p>{t.offlineNotice}</p></div><button type="button" className="secondary-button" onClick={closeEditor}>{t.cancel}</button></header>
      {!reviewing ? <form onSubmit={(event) => { event.preventDefault(); void prepareReview(); }}>
        <fieldset><legend>{t.groupIdentity}</legend><div className="month-end-form-grid">
          <label><span>{t.profileId}</span><input value={draft.profile_id || ""} readOnly={editorMode === "edit"} required pattern="[A-Za-z0-9._-]+" maxLength={64} onChange={(e) => update("profile_id", e.target.value)} /></label>
          <label><span>{t.system}</span><input value={draft.system_alias || ""} readOnly /></label><label><span>{t.client}</span><input value={draft.sap_client || ""} readOnly /></label>
          <label><span>{t.effectiveFrom}</span><input type="date" value={draft.effective_from || ""} required onChange={(e) => update("effective_from", e.target.value)} /></label><label><span>{t.effectiveTo}</span><input type="date" value={draft.effective_to || ""} onChange={(e) => update("effective_to", e.target.value)} /></label>
        </div></fieldset>
        <fieldset><legend>{t.groupCompany}</legend><div className="month-end-form-grid"><label><span>{t.companyCode}</span><input value={draft.company_code || ""} required maxLength={4} onChange={(e) => update("company_code", e.target.value.toUpperCase())} /></label><label><span>{t.ledgerOptional}</span><input value={draft.ledger || ""} maxLength={2} onChange={(e) => update("ledger", e.target.value.toUpperCase())} /></label></div></fieldset>
        <fieldset><legend>{t.groupAccounts}</legend><div className="month-end-form-grid"><ListField label={t.grir} value={draft.accounts.gr_ir} help={t.listHelp} onChange={(v) => update("accounts.gr_ir", v)} /><ListField label={t.openItem} value={draft.accounts.open_item_gl} help={t.listHelp} onChange={(v) => update("accounts.open_item_gl", v)} /><ListField label={t.autoClearing} value={draft.accounts.auto_clearing} onChange={(v) => update("accounts.auto_clearing", v)} /><ListField label={t.bank} value={draft.accounts.bank} onChange={(v) => update("accounts.bank", v)} /></div></fieldset>
        <fieldset><legend>{t.groupDocuments}</legend><div className="month-end-form-grid"><ListField label={t.incoming} value={draft.document_types.incoming_payments} onChange={(v) => update("document_types.incoming_payments", v)} /><ListField label={t.outgoing} value={draft.document_types.outgoing_payments} onChange={(v) => update("document_types.outgoing_payments", v)} /><ListField label={t.depreciation} value={draft.document_types.depreciation} onChange={(v) => update("document_types.depreciation", v)} /><ListField label={t.fx} value={draft.document_types.fx_valuation} onChange={(v) => update("document_types.fx_valuation", v)} /></div></fieldset>
        <fieldset><legend>{t.groupThresholds}</legend><div className="month-end-form-grid month-end-threshold-grid"><label><span>{t.age}</span><input type="number" min="0" max="3650" value={draft.thresholds.grir_age_days} onChange={(e) => update("thresholds.grir_age_days", Number(e.target.value))} /></label><label><span>{t.highAge}</span><input type="number" min="0" max="3650" value={draft.thresholds.grir_high_severity_days} onChange={(e) => update("thresholds.grir_high_severity_days", Number(e.target.value))} /></label><label><span>{t.qtyTolerance}</span><input inputMode="decimal" value={draft.thresholds.grir_quantity_tolerance} onChange={(e) => update("thresholds.grir_quantity_tolerance", e.target.value)} /></label><label><span>{t.amountTolerance}</span><input inputMode="decimal" value={draft.thresholds.grir_amount_tolerance} onChange={(e) => update("thresholds.grir_amount_tolerance", e.target.value)} /></label><label><span>{t.clearingTolerance}</span><input inputMode="decimal" value={draft.thresholds.auto_clearing_tolerance} onChange={(e) => update("thresholds.auto_clearing_tolerance", e.target.value)} /></label></div></fieldset>
        <fieldset><legend>{t.groupCo}</legend><div className="month-end-form-grid"><label><span>{t.controllingArea}</span><input value={draft.co.controlling_area || ""} onChange={(e) => update("co.controlling_area", e.target.value.toUpperCase())} /></label><ListField label={t.costCenters} value={draft.co.cost_centers} onChange={(v) => update("co.cost_centers", v)} /><ListField label={t.internalOrders} value={draft.co.internal_orders} onChange={(v) => update("co.internal_orders", v)} /></div></fieldset>
        <fieldset><legend>{t.groupStatuses}</legend><div className="month-end-form-grid"><ListField label={t.transferErrors} value={draft.status_mappings.billing_transfer_errors} onChange={(v) => update("status_mappings.billing_transfer_errors", v)} /><ListField label={t.postingErrors} value={draft.status_mappings.billing_posting_errors} onChange={(v) => update("status_mappings.billing_posting_errors", v)} /></div></fieldset>
        <fieldset><legend>{t.groupEvidence}</legend><p>{t.evidenceHelp}</p><div className="month-end-check-grid">{EVIDENCE_SOURCES.map((source) => <label key={source}><input type="checkbox" checked={(draft.approved_evidence_sources || []).includes(source)} onChange={(e) => update("approved_evidence_sources", e.target.checked ? [...draft.approved_evidence_sources, source] : draft.approved_evidence_sources.filter((item: string) => item !== source))} /><code>{source}</code></label>)}</div></fieldset>
        <fieldset><legend>{t.groupOwners}</legend><p>{t.ownerHelp}</p><div className="month-end-owner-list">{CHECKS.map((check) => <div key={check} className="month-end-owner-row"><code>{check}</code><input aria-label={`${check} ${t.department}`} placeholder={t.department} value={draft.owners?.[check]?.department || ""} onChange={(e) => update(`owners.${check}.department`, e.target.value)} /><input aria-label={`${check} ${t.owner}`} placeholder={t.owner} value={draft.owners?.[check]?.owner || ""} onChange={(e) => update(`owners.${check}.owner`, e.target.value)} /><input aria-label={`${check} ${t.remediation}`} placeholder={t.remediation} value={draft.owners?.[check]?.remediation || ""} onChange={(e) => update(`owners.${check}.remediation`, e.target.value)} /></div>)}</div></fieldset>
        <fieldset><legend>{t.groupPeriods}</legend><p>{t.periodHelp}</p><div className="month-end-period-list">{periodRows.map(([key, range], index) => <div className="month-end-period-row" key={`${key}-${index}`}><input aria-label={t.periodKey} placeholder="2026-13" value={key} onChange={(e) => { const rows = [...periodRows] as Array<[string, Json]>; rows[index] = [e.target.value, range as Json]; replacePeriods(rows); }} /><input aria-label={t.periodStart} type="date" value={(range as Json).start || ""} onChange={(e) => { const rows = [...periodRows] as Array<[string, Json]>; rows[index] = [key, { ...(range as Json), start: e.target.value }]; replacePeriods(rows); }} /><input aria-label={t.periodEnd} type="date" value={(range as Json).end || ""} onChange={(e) => { const rows = [...periodRows] as Array<[string, Json]>; rows[index] = [key, { ...(range as Json), end: e.target.value }]; replacePeriods(rows); }} /><button type="button" onClick={() => replacePeriods((periodRows as Array<[string, Json]>).filter((_, rowIndex) => rowIndex !== index))}>{t.remove}</button></div>)}</div><button type="button" className="secondary-button" onClick={() => replacePeriods([...(periodRows as Array<[string, Json]>), ["", { start: "", end: "" }]])}>{t.addPeriod}</button></fieldset>
        <footer className="month-end-editor-actions"><button type="button" className="secondary-button" onClick={() => void validate(draft, false)} disabled={busy}>{t.staticValidate}</button><button type="submit" className="primary-button" disabled={busy}>{t.review}</button></footer>
      </form> : <div className="month-end-summary"><h4>{t.summaryTitle}</h4><p>{t.summaryHint}</p><dl><div><dt>{t.profileId}</dt><dd>{draft.profile_id}</dd></div><div><dt>{t.system} / {t.client}</dt><dd>{draft.system_alias} / {draft.sap_client || "—"}</dd></div><div><dt>{t.companyCode}</dt><dd>{draft.company_code}</dd></div><div><dt>{t.effective}</dt><dd>{draft.effective_from} — {draft.effective_to || "∞"}</dd></div><div><dt>{t.ledger}</dt><dd>{draft.ledger || "leading"}</dd></div><div><dt>{t.groupAccounts}</dt><dd>{Object.entries(draft.accounts).map(([key, value]) => `${key}: ${joinList(value) || "—"}`).join(" · ")}</dd></div><div><dt>{t.groupDocuments}</dt><dd>{Object.entries(draft.document_types).map(([key, value]) => `${key}: ${joinList(value) || "—"}`).join(" · ")}</dd></div><div><dt>{t.groupThresholds}</dt><dd>{Object.entries(draft.thresholds).map(([key, value]) => `${key}: ${value}`).join(" · ")}</dd></div><div><dt>{t.groupCo}</dt><dd>{draft.co.controlling_area || "—"} · {joinList(draft.co.cost_centers) || "—"} · {joinList(draft.co.internal_orders) || "—"}</dd></div><div><dt>{t.groupStatuses}</dt><dd>{joinList(draft.status_mappings.billing_transfer_errors) || "—"} · {joinList(draft.status_mappings.billing_posting_errors) || "—"}</dd></div><div><dt>{t.groupEvidence}</dt><dd>{joinList(draft.approved_evidence_sources)}</dd></div><div><dt>{t.groupOwners}</dt><dd>{Object.keys(cleanProfile(draft).owners).join(", ") || "—"}</dd></div><div><dt>{t.groupPeriods}</dt><dd>{Object.entries(cleanProfile(draft).period_boundaries || {}).map(([key, value]) => `${key}: ${(value as Json).start}—${(value as Json).end}`).join(" · ") || "—"}</dd></div></dl><label className="month-end-confirm"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />{t.confirmation}</label><footer className="month-end-editor-actions"><button type="button" className="secondary-button" onClick={() => setReviewing(false)}>{t.back}</button><button type="button" className="primary-button" disabled={!confirmed || busy} onClick={() => void save()}>{busy ? t.saving : t.save}</button></footer></div>}
    </section>}
  </section>;
}
