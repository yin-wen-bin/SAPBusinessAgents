"""Run an AR cash-application acceptance case with an in-memory bank reference.

The reference is selected from an encrypted independent bank snapshot and is
passed to the baseline builder and three-stage runner only in memory/stdin.  It
is never written to the Canonical Case, argv, stdout, or public JSON artifacts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sap_business_agents_platform.acceptance import CanonicalTestCase, canonical_hash
from scripts.build_ar_cash_application_direct_baseline import build as build_baseline
from scripts.direct_sap_read import read_encrypted_rows


def _text(row: dict, field: str) -> str:
    target = field.casefold()
    for key, value in row.items():
        if str(key).casefold() == target:
            return str(value or "").strip()
    return ""


def _date_value(value: str) -> date | None:
    text = str(value or "").strip()
    for parser in (
        lambda item: date.fromisoformat(item[:10]),
        lambda item: datetime.strptime(item[:8], "%Y%m%d").date(),
    ):
        try:
            return parser(text)
        except (TypeError, ValueError):
            continue
    return None


def _prepare_case(source: Path, output: Path) -> dict:
    value = json.loads(source.read_text(encoding="utf-8"))
    value["case_id"] = str(value["case_id"]) + "-secure-reference"
    value["question"] = {
        "zh": "使用安全参数中的银行参考号，核对指定期间的来款、客户子分类账和发票清账关系。",
        "en": "Using the bank reference supplied as a secure parameter, reconcile the in-period receipt, customer subledger, and invoice-clearing relationship.",
    }
    conditions = value.get("business_conditions")
    if not isinstance(conditions, dict):
        conditions = {}
    conditions["receipt_reference_supplied_securely"] = True
    value["business_conditions"] = conditions
    value["input"].pop("receipt_reference", None)
    CanonicalTestCase.from_dict(value)
    if output.is_file():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if canonical_hash(existing) != canonical_hash(value):
            raise ValueError("secure reference case is immutable")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def _select_reference(case: dict, snapshot: Path) -> str:
    values = case["input"]
    company = str(values["company_code"])
    start = date.fromisoformat(str(values["date_from"]))
    end = date.fromisoformat(str(values["date_to"]))
    references = {
        _text(row, "BankReference")
        for row in read_encrypted_rows(snapshot)
        if _text(row, "CompanyCode") == company
        and (observed := _date_value(_text(row, "ValueDate"))) is not None
        and start <= observed <= end
        and _text(row, "DebitCreditCode") == "H"
        and _text(row, "BankReference")
    }
    if len(references) != 1:
        raise ValueError("test_data_gap_secure_reference_not_unique")
    return next(iter(references))


def _assert_not_persisted(reference: str, roots: list[Path]) -> None:
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*.json"))
        for path in paths:
            if path.is_file() and reference in path.read_text(encoding="utf-8", errors="ignore"):
                raise ValueError("secure_reference_leaked_to_public_json")


def run(args: argparse.Namespace) -> dict:
    output = args.output.resolve()
    case_path = output / "case.json"
    case = _prepare_case(args.source_case.resolve(), case_path)
    reference = _select_reference(case, args.bank_snapshot.resolve())
    baseline_path = output / "baseline.json"
    baseline = build_baseline(
        case_path,
        None,
        None,
        baseline_path,
        output / "direct-sources",
        bank_snapshot=args.bank_snapshot.resolve(),
        profile_path=args.profile.resolve(),
        reference_supplied=True,
        reference_value=reference,
    )
    command = [
        sys.executable,
        str((args.repository.resolve() / "scripts" / "run_three_stage_acceptance.py")),
        "--case", str(case_path),
        "--baseline", str(baseline_path),
        "--module", "FI",
        "--api-url", args.api_url.rstrip("/"),
        "--output", str(output / "three-stage"),
        "--agent-snapshot", str(args.agent_snapshot.resolve()),
        "--anchor-profile", str(args.profile.resolve()),
        "--sensitive-input-stdin",
    ]
    completed = subprocess.run(
        command,
        input=json.dumps({"receipt_reference": reference}),
        text=True,
        capture_output=True,
        cwd=args.repository.resolve(),
        timeout=args.timeout,
        check=False,
    )
    if reference in completed.stdout or reference in completed.stderr:
        raise ValueError("secure_reference_leaked_to_process_output")
    acceptance_path = output / "three-stage" / "acceptance.json"
    if not acceptance_path.is_file():
        raise RuntimeError("secure reference acceptance did not produce an artifact")
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    _assert_not_persisted(reference, [case_path, baseline_path, acceptance_path, output / "three-stage"])
    return {
        "verdict": acceptance.get("verdict"),
        "runner_exit_code": completed.returncode,
        "baseline_hash": baseline.get("result_hash"),
        "acceptance_hash": canonical_hash(acceptance),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run secure-reference AR acceptance without persisting the reference.")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-case", type=Path, required=True)
    parser.add_argument("--bank-snapshot", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path.home() / ".codex/secure/sap-direct-readonly.json")
    parser.add_argument("--agent-snapshot", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8775")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=3000)
    result = run(parser.parse_args())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
