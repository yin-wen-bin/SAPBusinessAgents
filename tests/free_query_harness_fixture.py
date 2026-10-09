"""Explicit offline Harness seam for service tests, never a legacy planner.

The model loop is scripted; reads still pass through the real capability Broker.
Native drivers and report finalization have their own dedicated test suites.
"""
from sap_business_agents_platform.harness import HarnessOutcome
from sap_business_agents_platform.engine import RunExecutionError
from sap_business_agents_platform.models import RunPresentation, PresentationBlock, LocalizedText


class ScriptedHarness:
    def __init__(self, planner):
        self.planner = planner

    def bind(self, app):
        self.store, self.broker = app.state.store, app.state.harness_broker
        return self

    async def interrupt(self, *args):
        return True

    async def steer(self, *args):
        return False

    async def run(self, run_id, query, thread_id=None, model=None, **kwargs):
        planner = self.planner
        planner.calls += 1
        planner.queries.append(query)
        if planner.clarify_once and planner.calls == 1:
            return HarnessOutcome('thread-001', 1, 'waiting_input', 'waiting_input', {},
                                  clarification_question='Which purchase order?')
        plan = {'service_name': 'API_PURCHASEORDER_PROCESS_SRV', 'odata_version': '2.0',
                'entity_set': 'A_PurchaseOrder', 'http_method': planner.method,
                'plan_kind': 'direct', 'filters': [{'field': 'PurchaseOrder', 'operator': 'eq',
                                                 'value': '4500000001', 'value_type': 'string'}]}
        if planner.top is not None:
            plan['top'] = planner.top
        plan = getattr(planner, 'fixture_plan', plan)
        token = self.broker.open_session(run_id)
        try:
            await self.broker.handle(run_id, token, 'sap_catalog_search', {'query': plan['service_name']})
            schema = await self.broker.handle(run_id, token, 'sap_schema_get',
                {'service_name': plan['service_name'], 'odata_version': '2.0', 'entity_sets': ['A_PurchaseOrder']})
            validation = await self.broker.handle(run_id, token, 'sap_query_validate', {'plan': plan})
            if not validation.get('ok'):
                raise RunExecutionError('Scripted query rejected.', code=validation.get('code') or 'free_query_plan_rejected')
            response = await self.broker.handle(run_id, token, 'sap_query_execute', {'plan': plan})
            if not response.get('ok'):
                raise RunExecutionError('Scripted query rejected.', code=response.get('code') or 'free_query_plan_rejected')
            refs = [response['evidence_ref']]
            if getattr(planner, 'fixture_skill_id', None):
                skill_input = {'sap': response}
                gap = await self.broker.handle(run_id, token, 'sap_evidence_assess', {
                    'evidence_refs': refs, 'missing_evidence': ['fixture_review'],
                    'skill_id': planner.fixture_skill_id, 'skill_input': skill_input})
                assert gap.get('skill_eligible') and gap.get('gap_token'), gap
                skill = await self.broker.handle(run_id, token, 'sap_skill_execute', {
                    'skill_id': planner.fixture_skill_id, 'input': skill_input, 'gap_token': gap['gap_token']})
                assert skill.get('ok'), skill
                refs.append(skill['evidence_ref'])
            calls, evidence = self.broker.snapshot(run_id)
            complete = response.get('source_complete') is True and planner.top is None
            summary = {'zh': 'One read-only SAP record was found.', 'en': 'One read-only SAP record was found.'}
            presentation = RunPresentation(title=LocalizedText(zh='结果', en='Result'),
                blocks=[PresentationBlock(type='text', text=LocalizedText(**summary))])
            self.store.update_harness_state(run_id, {'thread_id': thread_id or 'thread-001', 'turn_count': 1})
            return HarnessOutcome(thread_id or 'thread-001', 1, 'completed' if complete else 'inconclusive',
                'completed', summary, source_complete=complete, business_complete=complete,
                executed_plans=[plan], tool_calls=calls, evidence=evidence,
                evidence_refs=refs, presentation=presentation)
        finally:
            self.broker.close_session(run_id)
