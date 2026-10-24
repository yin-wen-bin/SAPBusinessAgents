"""Static sample readiness only; no SDK, metadata or SAP I/O."""
import copy
import pytest
from sap_business_agents_platform.sample_discovery import SampleDiscoveryContext
from tests.test_agent_sample_discovery import manifest

@pytest.mark.parametrize('operator',['le','lt','ge','gt'])
def test_required_boundary_is_manual_even_for_partial_selection(operator):
    value=manifest(); execution=value['execution']; schema=execution['inputSchema']
    schema['properties']['cutoff']={'type':'string','format':'date'}; schema['required'].append('cutoff')
    plan=execution['steps'][0]['inputMapping']['plan']
    plan['select_fields'].append('PostingDate')
    plan['filters'].append({'field':'PostingDate','operator':operator,'value':'{{input.cutoff}}'})
    before=copy.deepcopy(value)
    result=SampleDiscoveryContext(value,{'company_code':'1710'},2,selected_fields=['customer']).readiness()
    field=next(x for x in result['fields'] if x['field']=='cutoff')
    assert field['reason']=='range_boundary' and field['status']=='manual' and not field['sources']
    assert result['can_start'] is False and 'cutoff' in result['blocking_fields']
    ready=SampleDiscoveryContext(value,{'company_code':'1710','cutoff':'2022-12-31'},2,selected_fields=['customer']).readiness()
    assert ready['can_start'] is True
    assert value==before

def test_absent_mapping_is_not_misreported_as_boundary():
    value=manifest(); value['execution']['inputSchema']['properties']['unknown']={'type':'string'}
    result=SampleDiscoveryContext(value,{'company_code':'1710'},2).readiness()
    assert next(x for x in result['fields'] if x['field']=='unknown')['reason']=='mapping_missing'
