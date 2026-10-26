"""Synthetic target authentication service only; no SDK or model calls."""
from types import SimpleNamespace
import pytest
from sap_business_agents_platform.sdk_manager import SDKManagerError
from sap_business_agents_platform.workbuddy_sdk_target_audit import TargetAdmissionAudit


@pytest.mark.parametrize('failure',[None,'pending','identity','revision','site','old_environment','unconfirmed_target','missing_job'])
def test_target_audit_needs_current_target_identity_and_owned_model_origin(monkeypatch,failure):
    account={'authenticated':True,'authentication_context_status':'current','revision':9}
    binding={'source':'official_cli','authenticated':True,'identity_digest':'original','service_site_digest':'china'}
    state={'default_model_id':'glm-5.3','authentication_revision':9,'authentication_identity_digest':'original','service_site_digest':'china'}
    candidate={'authentication_environment':'new'};release={'environment_digest':'new'}
    if failure=='pending':account['authentication_context_status']='check_required'
    if failure=='identity':binding['identity_digest']='other'
    if failure=='revision':account['revision']=10
    if failure=='site':binding['service_site_digest']='international'
    if failure=='old_environment':release={'environment_digest':'old'}
    if failure=='unconfirmed_target':candidate['authentication_environment']='old'
    env=SimpleNamespace(state=lambda:candidate,release=lambda:release)
    calls=[];service=SimpleNamespace(view=lambda p:account,_account=lambda p:{'confirmed_binding':binding},
        _adapter=lambda p:SimpleNamespace(workbuddy=SimpleNamespace(environment=env)),assert_binding=lambda *a:calls.append(a))
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_sdk_target_audit.model_check_origin',lambda *a:failure!='missing_job')
    audit=TargetAdmissionAudit(service,{'id':'synthetic'},
        {'environment_digest':'new','authentication_revision':9},{'model':'glm-5.3','environment_digest':'new'})
    if failure is None:
        result=audit({'environment_digest':'new'},state)
        assert result['admitted'] is True and result['authentication_environment']=='new' and calls
    else:
        with pytest.raises(SDKManagerError):audit({'environment_digest':'new'},state)
