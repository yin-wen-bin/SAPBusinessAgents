"""Native test bootstrap: real application, owned query store, existing tools.

This helper starts no AI session. The product retains its normal Runtime gates.
"""
import json
import sys
from dataclasses import replace
from pathlib import Path

configuration = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
target = Path(configuration["cwd"]).resolve()
sys.path.insert(0, str(target / "src"))
from sap_business_agents_test_agent.inventory import source_files
from sap_business_agents_test_agent.common import digest
if digest(source_files(target)) != configuration["source_digest"]:
    raise RuntimeError("test_target_source_changed")
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.app import create_app
import uvicorn

settings = replace(Settings.from_env(Path(configuration["platform_configuration_root"])),
    data_root=target / ".local-data", draft_root=target / ".prototype/authoring",
    runtime_state_root=Path(configuration["runtime_state_reference"]),
    internal_api_url=configuration["api_url"], local_ui_origins=(configuration["site_url"],))
application = create_app(settings)
identity = json.loads((target / "site/public/__sapba_auto_test_target.json").read_text())
identity.update(data_root=str(settings.data_root), source_digest=configuration["source_digest"])

@application.get("/__sapba_auto_test_target.json", include_in_schema=False)
def identity_for_owned_test_target():
    return identity

uvicorn.run(application, host="127.0.0.1", port=int(configuration["api_url"].rsplit(":", 1)[1]))
