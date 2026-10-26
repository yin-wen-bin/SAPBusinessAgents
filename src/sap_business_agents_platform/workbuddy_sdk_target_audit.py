"""Read-only target auditor for the private controlled upgrade/rollback hook."""
import copy
from .sdk_update import failure
from .workbuddy_qualification import model_check_origin


class TargetAdmissionAudit:
    """Consume already-produced platform evidence; never perform a probe/login.

    The authentication service must be explicitly configured for the candidate
    environment by maintenance tooling, not the currently active environment.
    Merely copying its old account record cannot satisfy observation/current.
    """
    def __init__(self,authentication,certificate,model_check,snapshot):
        self.authentication=authentication
        self.certificate=copy.deepcopy(certificate)
        self.model_check=copy.deepcopy(model_check)
        self.snapshot=copy.deepcopy(snapshot)

    def __call__(self,release,state):
        service=self.authentication;key=release['environment_digest'];model=state.get('default_model_id')
        account=service.view('workbuddy');binding=service._account('workbuddy').get('confirmed_binding') or {}
        adapter_environment=service._adapter('workbuddy').workbuddy.environment
        candidate_state=adapter_environment.state()
        if (account.get('authenticated') is not True or account.get('authentication_context_status')!='current'
                or account.get('failure_code') or account.get('revision')!=state.get('authentication_revision')
                or binding.get('source')!='official_cli' or binding.get('authenticated') is not True
                or binding.get('identity_digest')!=state.get('authentication_identity_digest')
                or not binding.get('identity_digest')
                or binding.get('service_site_digest')!=state.get('service_site_digest')
                or adapter_environment.release()['environment_digest']!=key
                or candidate_state.get('authentication_environment')!=key):
            raise failure('workbuddy_update_evidence_required','version_confirmation')
        service.assert_binding('workbuddy',self.snapshot)
        check=self.model_check
        if (check.get('environment_digest')!=key or check.get('authentication_revision')!=account['revision']
                or self.snapshot.get('model')!=model or self.snapshot.get('environment_digest')!=key
                or not model_check_origin(adapter_environment,check,model)):
            raise failure('workbuddy_update_evidence_required','version_confirmation')
        return {'certificate':copy.deepcopy(self.certificate),'model_check':copy.deepcopy(check),
                'snapshot':copy.deepcopy(self.snapshot),'authentication_environment':key,'admitted':True}
