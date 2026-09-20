import json
from pathlib import Path


def _manifest_path():
    candidates = [
        Path(__file__).parents[1] / 'agent.json',
        Path(__file__).parents[2] / 'manifest.json',
    ]
    return next(path for path in candidates if path.exists())


def test_generated_manifest_is_read_only():
    manifest = json.loads(_manifest_path().read_text(encoding='utf-8'))
    assert manifest['schemaVersion'] == 2
    assert all(step.get('readOnly', True) for step in manifest['execution']['steps'])


def test_input_cardinality_and_optional_scope_contract():
    manifest = json.loads(_manifest_path().read_text(encoding='utf-8'))
    schema = manifest['execution']['inputSchema']
    assert schema['required'] == ['purchase_order']
    assert schema['properties']['purchase_order']['type'] == 'array'
    assert schema['properties']['sales_order']['type'] == 'string'

    steps = {step['id']: step for step in manifest['execution']['steps']}
    for step_id in ('sap_query_1', 'sap_query_2', 'sap_query_3'):
        purchase_filter = steps[step_id]['request']['plan']['filters'][0]
        assert purchase_filter['operator'] == 'in'
        assert purchase_filter['value'] == '{{input.purchase_order}}'

    material_filter = steps['sap_query_3']['request']['plan']['filters'][1]
    assert material_filter['value'] == '{{input.material}}'
    assert material_filter['omitIfEmpty'] == '{{input.material?}}'

    sales_order_filter = steps['sap_query_5']['request']['plan']['filters'][3]
    assert sales_order_filter['value'] == '{{input.sales_order}}'
    assert sales_order_filter['omitIfEmpty'] == '{{input.sales_order?}}'
