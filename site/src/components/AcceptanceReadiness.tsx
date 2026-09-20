import type { Locale } from "../lib/types";

const labels: Record<string, [string, string]> = {
  contract_version_required: ["请补齐新版业务验收定义。", "Complete the versioned business acceptance definition."],
  contract_definition_missing: ["请说明结果粒度、纳入范围和业务状态的判断依据。", "Define record grain, scope and business-status criteria."],
  contract_records_missing: ["尚未声明结构化业务明细。", "Typed canonical business records are missing."],
  contract_keys_missing: ["请定义可唯一识别业务记录的字段。", "Define stable business record keys."],
  contract_field_unmapped: ["验收字段未映射到必需的类型化业务输出。", "A compared field is not mapped to a required typed output."],
  contract_key_nullable: ["业务记录标识不能是未知值。", "Business keys cannot be unknown."],
  contract_technical_fact: ["执行完成状态不能代替业务结论。", "Execution status cannot substitute for a business conclusion."],
  contract_enum_missing: ["请声明业务状态的允许值。", "Declare allowed business-state values."],
  contract_fact_definition_missing: ["请说明该业务字段的含义和证据来源。", "Define the field meaning and evidence source."],
  contract_metric_unmapped: ["验收指标缺少类型化输出。", "A compared metric has no typed output."],
  contract_technical_metric: ["查询次数等技术指标不适合比较业务结果。", "Query diagnostics cannot serve as business comparison metrics."],
  contract_metric_definition_missing: ["请说明指标范围、去重规则及零值和未知值的区别。", "Define metric scope, deduplication, zero and unknown."],
  contract_root_unmapped: ["请补齐业务状态和各项完整性输出。", "Expose business status and completeness outputs."],
  contract_invariant_invalid: ["计数或求和校验规则无效。", "The declared count or sum invariant is invalid."],
  contract_field_invalid: ["业务字段缺失、类型错误或状态值未定义。", "A business field is missing, mistyped or has an undeclared value."],
  contract_business_key_invalid: ["业务记录标识缺失或重复。", "Business keys are missing or duplicated."],
  contract_metric_invalid: ["业务指标缺失或类型不正确。", "A business metric is missing or mistyped."],
  contract_metric_invariant_failed: ["指标与实际业务明细计数或求和不一致。", "The metric disagrees with its record count or sum."],
  contract_business_status_invalid: ["整体业务状态不在已定义的范围内。", "The root business status is undeclared."],
  contract_completeness_invalid: ["完整性未明确声明。", "Completeness was not declared explicitly."],
  contract_report_records_mismatch: ["页面明细与规范业务记录不一致。", "Displayed and canonical records disagree."],
  contract_report_metric_mismatch: ["页面指标与规范业务指标不一致。", "Displayed and canonical metrics disagree."],
  contract_limitation_missing: ["业务报告缺少必要的能力边界说明。", "A required business limitation is missing."],
  contract_required_assessments_missing: ["请明确声明是否需要库存FIFO等专用评估；不需要时保存空列表。", "Explicitly declare whether specialized assessments such as inventory FIFO are required; save an empty list when none are needed."],
  contract_required_assessments_invalid: ["专用评估要求必须是无重复项的列表。", "Required assessments must be a list without duplicates."],
  contract_required_assessment_unknown: ["包含平台尚未支持的专用评估要求。", "The contract contains an unsupported specialized assessment."],
  acceptance_projection_field_missing: ["验收报告缺少契约要求的字段。", "The acceptance report is missing a contract field."],
  acceptance_projection_field_unexpected: ["验收报告包含契约未声明的字段。", "The acceptance report contains a field not declared by the contract."],
  acceptance_projection_type_invalid: ["验收报告字段类型与契约不一致。", "An acceptance report field has the wrong type."],
  acceptance_projection_enum_invalid: ["验收报告字段值不在契约允许范围内。", "An acceptance report field value is outside the contract enum."],
  acceptance_report_schema_invalid: ["验收报告结构不符合当前契约。", "The acceptance report structure does not match the current contract."],
  inventory_fifo_assessment_required: ["本次验收明确要求有效的库存FIFO评估，但尚未取得。", "This acceptance explicitly requires a valid inventory FIFO assessment, but none is available."],
};

export function ContractIssues({ issues, locale }: { issues: any[]; locale: Locale }) {
  return <ul>{issues.map((issue, i) => <li key={i}>{(labels[issue.code] || ["验收定义或输出需要完善。", "The acceptance definition or output needs attention."])[locale === "zh" ? 0 : 1]} {issue.path && <code style={{ overflowWrap: "anywhere" }}>{issue.path}</code>}</li>)}</ul>;
}

export default function AcceptanceReadiness({ value, locale }: { value: any; locale: Locale }) {
  if (!value || value.status === "reused") return null;
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  return <div className="acceptance-inline-summary" aria-live="polite">
    <strong>{tr("验收准备检查", "Acceptance readiness")}</strong>
    <p>{value.status === "ready" ? tr("业务定义与本修订试运行输出已通过离线检查，可开始正式验收。此检查不代表业务验收通过。", "The definition and current trial passed offline checks. Formal acceptance is ready; business correctness is not certified.") : value.status === "trial_required" ? tr("业务定义检查通过，请完成当前修订的只读试运行。", "The definition passed. Complete a trial for this revision.") : tr("请先完善以下问题，再开始正式验收。您仍可保存草稿和试运行。", "Resolve these issues before formal acceptance. Saving and trials remain available.")}</p>
    <ContractIssues issues={value.issues || []} locale={locale} />
  </div>;
}
