"""Read-only execution facts, never an admission decision or model claim."""
import json

ACTIVITY_EVENTS = ('agent_runtime_turn_started','agent_runtime_connected','agent_runtime_query_submitted',
    'agent_runtime_activity','agent_runtime_response_received','agent_runtime_result_received',
    'agent_runtime_sdk_closing','agent_runtime_sdk_closed','agent_runtime_turn_completed',
    'agent_runtime_turn_failed','workbuddy_native_failure','workbuddy_formatter_progress',
    'workbuddy_execution_failed','workbuddy_native_wait')
PHASES = ('received','preparing','reading_sap','validating_evidence','preparing_result')

def project(store, record, iteration):
    reuse = bool(iteration and iteration.get('execution_action') == 'reinterpret')
    names = (*ACTIVITY_EVENTS,'run_started','plan_created','plan_grounded','evidence_received',
             'summary_started','harness_completed')
    with store._connect() as connection:
        rows = connection.execute('SELECT DISTINCT event_type FROM events WHERE run_id=? AND event_type IN ('+
            ','.join('?' for _ in names)+')',(record.run_id,*names)).fetchall()
        latest = connection.execute('SELECT sequence,event_type,data_json FROM events WHERE run_id=? AND event_type IN ('+
            ','.join('?' for _ in ACTIVITY_EVENTS)+') ORDER BY sequence DESC LIMIT 1',(record.run_id,*ACTIVITY_EVENTS)).fetchone()
    observed = {r['event_type'] for r in rows}
    activity = None
    if latest:
        data = json.loads(latest['data_json'])
        activity = {'sequence':latest['sequence'],'type':latest['event_type'],
            'data':{k:data[k] for k in ('worker_wait_state','assistant_message_count') if k in data}}
    done = record.status.value in {'completed','inconclusive'}
    stopped = record.status.value in {'failed','cancelled'}
    phase = record.progress.phase
    complete = {
        'received':bool(record.started_at or 'run_started' in observed),
        'preparing':bool(observed & {'plan_created','plan_grounded','agent_runtime_connected'}),
        'reading_sap':bool('evidence_received' in observed),
        'validating_evidence':bool(observed & {'summary_started','harness_completed'}),
        'preparing_result':done,
    }
    if reuse:
        complete['preparing'] = bool(activity or done)
        complete['validating_evidence'] = done
    steps=[]
    for name in PHASES:
        state = ('skipped' if reuse and name=='reading_sap' else
                 'stopped' if stopped and name==phase else
                 'completed' if complete[name] else
                 'active' if name==phase and not done and not stopped else 'pending')
        steps.append({'phase':name,'state':state})
    return {'run_id':record.run_id,'execution_action':iteration.get('execution_action') if iteration else None,
            'evidence_reused':reuse,'phase':phase,'steps':steps,'activity':activity,
            'stage_indicator_only':True}
