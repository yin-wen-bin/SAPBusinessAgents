"""Real owned synthetic worker pause and journal restart compatibility."""
import asyncio
import json
import time
import pytest

from tests.test_workbuddy_isolation import supervisor
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError,digest
from sap_business_agents_platform.workbuddy_job_index import rebuild,pending


def test_owned_pause_is_interrupted_control_and_survives_journal_rebuild(tmp_path,monkeypatch):
    owner,binding=supervisor(tmp_path,monkeypatch)
    async def tool(*_):
        return {'ok':True,'control':'waiting_input','_workbuddy_stop_after_response':True,
                '_runtime_report_stop_code':'runtime_input_requested'}
    with pytest.raises(WorkBuddyError,match='runtime_input_requested'):
        asyncio.run(owner.run(task_id='pause',snapshot=binding,operation='review_workflow',seconds=15,
            payload={'scenario':'tool'},tool_handler=tool))
    record=json.loads((owner.environment.root/'jobs'/(digest('pause')+'.json')).read_bytes())
    assert record['status']=='interrupted' and record['control_event']=='waiting_input'
    assert record['cleanup_complete'] is True and not owner.pending()
    rebuild(owner.environment,clearance=lambda:{'all_writers_checked':True,'old_writers':[],
        'active_jobs':[],'cleanup_pending':[],'process_inventory_digest':digest('synthetic owned worker exited'),
        'checked_at':time.time()})
    assert not pending(owner.environment.root)
    assert json.loads((owner.environment.root/'jobs'/(digest('pause')+'.json')).read_bytes())==record
