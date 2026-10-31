const dimensions = {
  summary_amounts: ['汇总金额', 'Summary amounts'], detail_amounts: ['明细金额', 'Detail amounts'],
  business_facts: ['业务事实', 'Business facts'], coverage: ['业务覆盖范围', 'Business coverage'],
};
const states = {verified: ['已验证', 'Verified'], needs_evidence: ['待补证', 'Evidence required'],
  failed: ['校验失败', 'Validation failed'], not_requested: ['未涉及', 'Not applicable']};
const reasons = {
  answer_scope_coverage_unproven: ['证据尚未覆盖用户确认的全部范围。', 'Evidence does not yet cover the full confirmed scope.'],
  source_business_completeness_unproven: ['来源中仍有业务分类或配置需要核实。', 'Source classifications or configuration still require confirmation.'],
  report_row_set_mismatch: ['展示明细与来源记录集合不一致。', 'Displayed rows do not match the source record set.'],
  narrative_business_claim_unproven: ['这段业务判断尚缺少可核对的证明。', 'This business statement still requires verifiable support.'],
  business_claim_binding_required: ['这段业务判断尚未绑定可核对的证明。', 'This business statement has no verifiable binding.'],
};

export function verificationRows(value, locale = 'zh') {
  if (value?.version !== 'sapba.report-verification/2') return [];
  const index = locale === 'zh' ? 0 : 1;
  return Object.entries(dimensions).map(([key, label]) => {
    const item = value[key];
    const status = states[item?.status] ? item.status : 'needs_evidence';
    return {key, label: label[index], status, text: states[status][index],
      issues: (Array.isArray(item?.issues) ? item.issues : []).map(issue => ({
        code: String(issue.code || 'verification_evidence_required'), path: String(issue.path || ''),
        message: (reasons[issue.code] || ['该内容尚缺少可核对的依据。', 'This content still requires verifiable support.'])[index],
        evidence_ref: String(issue.evidence_ref || '')}))};
  });
}
