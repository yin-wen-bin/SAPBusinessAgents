import type { ExecutionInputSchema, ExecutionInputProperty, Locale } from "../lib/types";
import { inputRequirement, inputType, localText, publicBranchRequirements, publicInput, publicValues } from "../lib/agentDraft";

type Props = {
  schema: ExecutionInputSchema | ExecutionInputProperty;
  values: Record<string, any>; onChange: (value: Record<string, any>) => void;
  secrets: Record<string, string>; onSecrets: (value: Record<string, string>) => void;
  locale: Locale; errors?: Record<string, string>; disabled?: boolean; prefix?: string;
};

/** React adapter for the same declared inputs and runtime-field-grid presentation as AgentRunPanel. */
export default function AgentDraftInputs({ schema, values, onChange, secrets, onSecrets, locale, errors = {}, disabled = false, prefix = "" }: Props) {
  const update = (key: string, value: any) => onChange({ ...values, [key]: value });
  const branch = publicBranchRequirements(schema as ExecutionInputSchema, values);
  const schemaError = errors[prefix ? `${prefix}._schema` : "_schema"];
  return <div className="runtime-field-grid">{schemaError && <p className="runtime-field-error" role="alert">{schemaError}</p>}{Object.entries(schema.properties || {}).filter(([, property]) => publicInput(property)).map(([key, property]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    const id = `draft-input-${path}`;
    const label = localText(property.title, locale) || key;
    const sensitive = property["x-sapba-sensitive"] === true;
    const value = sensitive ? secrets[path] || "" : values[key];
    const required = branch.required.includes(key) && !property["x-sapba-server-default"];
    const requirement = <span className="draft-field-requirement">{inputRequirement(schema, key, values, locale)}</span>;
    const help = localText(property.description, locale);
    const type = inputType(property);
    const childProps = { secrets, onSecrets, locale, errors, disabled };
    if (type === "object" || (type === "array" && property.items?.type === "object")) {
      const list = Array.isArray(value) ? value : [];
      return <fieldset className="draft-object-fields" key={path}><legend>{label}{requirement}</legend>{help && <p>{help}</p>}
        {type === "object" ? <AgentDraftInputs {...childProps} schema={property} values={value || {}} onChange={(next) => update(key, next)} prefix={path} /> : <>
          {list.map((row, index) => <div className="runtime-object-array-row" key={index}><h4>{label} {index + 1}</h4><AgentDraftInputs {...childProps} schema={property.items!} values={row} onChange={(next) => update(key, list.map((item, number) => number === index ? next : item))} prefix={`${path}.${index}`} /><div className="agent-actions">
            <button type="button" className="agent-secondary-action" disabled={disabled || list.length >= (property.maxItems ?? 50)} onClick={() => update(key, [...list, publicValues(property.items!, row)])}>{locale === "zh" ? "复制" : "Copy"}</button>
            <button type="button" className="agent-secondary-action" disabled={disabled} onClick={() => { update(key, list.filter((_, number) => number !== index)); onSecrets(Object.fromEntries(Object.entries(secrets).filter(([name]) => !name.startsWith(`${path}.`)))); }}>{locale === "zh" ? "删除此行" : "Remove row"}</button>
          </div></div>)}
          <button type="button" className="agent-secondary-action" disabled={disabled || list.length >= (property.maxItems ?? 50)} onClick={() => update(key, [...list, publicValues(property.items!, {}, true)])}>{locale === "zh" ? "添加一行" : "Add row"}</button>
        </>}{errors[path] && <p className="runtime-field-error" role="alert">{errors[path]}</p>}</fieldset>;
    }
    const set = (next: any) => sensitive ? onSecrets({ ...secrets, [path]: next }) : update(key, next);
    const common = { id, disabled, required, "aria-invalid": Boolean(errors[path]), "aria-describedby": `${id}-help ${id}-error` };
    return <div className="draft-input-field" key={path}><label htmlFor={id}>{label}{requirement}</label><small id={`${id}-help`} className="runtime-field-help">{help}{property["x-sapba-server-default"] && (locale === "zh" ? " 留空使用服务端业务日期。" : " Leave empty for the server business date.")}{sensitive && (locale === "zh" ? " 仅通过安全参数提交，不进入修改对话。" : " Submitted securely; never included in revision chat.")}</small>
      {type === "boolean" ? <input {...common} type="checkbox" checked={value === true} onChange={(event) => set(event.target.checked)} /> : property.enum ? <select {...common} value={value === undefined ? "" : String(value)} onChange={(event) => set(property.enum!.find((item) => String(item) === event.target.value) ?? "")}><option value="">{locale === "zh" ? "请选择" : "Select"}</option>{property.enum.map((item, index) => <option key={index} value={String(item)}>{localText(property["x-sapba-display"]?.labels?.[String(item)], locale) || String(item)}</option>)}</select> : type === "array" ? <textarea {...common} className="runtime-list-input" rows={3} value={Array.isArray(value) ? value.join("\n") : value || ""} placeholder={localText(property.placeholder, locale)} onChange={(event) => set(event.target.value)} /> : <input {...common} type={sensitive ? "password" : property.format === "date" ? "date" : type === "integer" || type === "number" ? "number" : "text"} value={value ?? ""} autoComplete={sensitive ? "new-password" : "off"} placeholder={localText(property.placeholder, locale)} minLength={property.minLength} maxLength={property.maxLength} min={property.minimum} max={property.maximum} step={type === "integer" ? 1 : "any"} onChange={(event) => set((type === "number" || type === "integer") && event.target.value !== "" ? Number(event.target.value) : event.target.value)} />}
      <small id={`${id}-error`} className="runtime-field-error" role={errors[path] ? "alert" : undefined}>{errors[path]}</small>
    </div>;
  })}</div>;
}
