"""Offline test evidence only. Never install into a real SDK state."""
from pathlib import Path
from sap_business_agents_platform import workbuddy_integration as integration
from sap_business_agents_platform.workbuddy_environment import atomic_json, file_hash, digest

def certificate(manager, value, operations=None):
    operations = operations or {"review_workflow": ["bounded"], "acceptance_baseline": ["bounded"],
                                "acceptance_free_query": ["bounded"]}
    target = integration.binding(value)
    folder = manager.environment.root / "certification-test-evidence"
    folder.mkdir(parents=True, exist_ok=True)
    def artifact(name, report):
        path = folder / (name + ".json")
        atomic_json(path, {"target": target, "kind": "sdk_infrastructure_check", "complete": True, **report})
        return {"path": path.relative_to(manager.environment.root).as_posix(), "sha256": file_hash(path)}
    import xml.etree.ElementTree as xml
    suite = xml.Element("testsuite")
    for module, name in sorted(set(integration.CORE_TESTS.values())):
        xml.SubElement(suite, "testcase", classname="tests." + module, name=name)
    report = folder / "simulated-test-report.xml"
    xml.ElementTree(suite).write(report, encoding="utf-8")
    sources = {}
    for filename, sha in integration.checker_manifest().items():
        path = folder / filename
        path.write_bytes((Path(__file__).parent / filename).read_bytes())
        sources[filename] = {"path": path.relative_to(manager.environment.root).as_posix(), "sha256": sha}
    core = artifact("core", {"assertions": {name: True for name in integration.CORE},
        "checker_contract": integration.CERTIFICATE, "checker_files": integration.checker_manifest(), "checker_sources": sources,
        "test_report": {"path": report.relative_to(manager.environment.root).as_posix(), "sha256": file_hash(report)}})
    origin = artifact("simulated-sdk-origin", {"protocol": __import__('sap_business_agents_platform.workbuddy_environment', fromlist=['PROTOCOL']).PROTOCOL,
        "environment_digest": value["environment_digest"], "status": "completed", "cleanup_complete": True,
        "process_identity": "offline-simulated-only", "result_metadata": {"structured_terminal": True}})
    evidence = {"target": target, "core": {name: core for name in integration.CORE}, "transport_origin": origin, "operations": {}}
    for op, modes in operations.items():
        evidence["operations"][op] = {mode: artifact(op + mode,
            {"operation": op, "permission_mode": mode, "cleanup_complete": True}) for mode in modes}
    return integration.audit(manager.environment, value, evidence)

def install_certificate(manager, value):
    from sap_business_agents_platform.runtime_policy import OPERATIONS
    from sap_business_agents_platform.workbuddy_qualification import required_modes
    cert = certificate(manager, value, {op: list(required_modes(op)) for op in OPERATIONS})
    manager.environment.mutate(lambda state: state.update(runtime_admission_policy=integration.POLICY,
        sdk_integration_records={**state.get("sdk_integration_records", {}), cert["id"]: cert}))
    return cert

async def probe_result(manager, kwargs, actual="concrete-model", source="assistant_message"):
    import json
    challenge = kwargs["tool_handler"].__self__
    echo = await kwargs["tool_handler"]("compatibility_echo", {"challenge": challenge.challenge})
    text = json.dumps({"status": "ok", **echo, "optional": None, "count": 1, "nested": {"kind": "echo"}})
    atomic_json(manager.environment.root / "jobs" / (digest(kwargs["task_id"]) + ".json"),
        {"status": "completed", "cleanup_complete": True, "operation": "model_check", "runtime_model": kwargs["snapshot"]["model"],
         "result_metadata": {"structured_terminal": True, "actual_model": actual, "identity_source": source, "output_sha256": digest(text)}})
    return {"text": text,
        "actual_model": actual, "model_identity_source": source, "output_format": "native_json_schema"}
