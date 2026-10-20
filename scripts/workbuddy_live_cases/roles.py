"""Role validation delegates to the existing verifier using the batch binding."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / ".local-data/workbuddy-validation"
RUN_ROOT = HERE / "roles-data"
REPORT = RUN_ROOT / "report.json"
spec = importlib.util.spec_from_file_location("workbuddy_batch_roles", ROOT / "scripts/verify-runtime-role-matching.py")
role_case = importlib.util.module_from_spec(spec)
spec.loader.exec_module(role_case)


async def main():
    result = await role_case.verify(SimpleNamespace(provider="workbuddy", data_root=RUN_ROOT, batch=HERE))
    if result:
        raise SystemExit(result)
