"""Version 1 live-case evidence checker, independent of ledger/reuse policy.

Extracted unchanged from qualification protocol 2. Governance or reporting
changes do not alter this full-file execution-evidence contract. Changes here
still invalidate the checker binding and require explicit compatibility review.
"""
from pathlib import Path

from .runtime_validation import ValidationCase, check_result, digest
from .runtime_validation_store import read, verify_archive, verify_artifacts

VERSION = 'sapba.validation.live-evidence/1'


def valid(record):
    return isinstance(record, dict) and record.get("id") == digest({k: v for k, v in record.items() if k != "id"})


def verify_pass(environment, record):
    if not valid(record) or record.get("kind") != "case_pass" or record.get("execution_kind") != "live":
        return False
    try:
        source = record["source_snapshot"]
        verify_archive(source["directory"], source["digest"])
        root = Path(record["artifact_root"])
        verify_artifacts(root, record["artifacts"])
        if not (root / record['checker_report']['path']).resolve().is_relative_to(root.resolve()):
            return False
        report = read(root / record["checker_report"]["path"])
        if digest(report) != record["checker_report"]["digest"] or report != record["result"]:
            return False
        if report.get("status") != "passed" or not record.get("owned_jobs"):
            return False
        original_case = ValidationCase(**record['case_definition'])
        check_result(original_case, report)
        if (original_case.digest != record['binding']['case_digest'] or report['mode'] != record['binding']['mode']
                or report['environment_digest'] != record['environment_digest']):
            return False
        operations = set()
        for reference in record["owned_jobs"]:
            job = read(environment.root / "jobs" / (digest(reference["job_id"]) + ".json"))
            if (digest(job) != reference["digest"] or job.get("status") != "completed"
                    or job.get("cleanup_complete") is not True or job.get("validation_job") is not True
                    or job.get("result_metadata", {}).get("structured_terminal") is not True
                    or job.get("result_metadata", {}).get("actual_model") != record["binding"]["runtime"]["actual_model"]
                    or job.get('result_metadata', {}).get('identity_source') != record['binding']['runtime']['model_identity_source']
                    or job.get("validation_case_binding") != record["binding"]
                    or job.get('runtime_digest') != report['binding_digest']
                    or job.get('permission_mode') != record['binding']['mode']
                    or job.get('task_id') != reference['job_id']
                    or job.get('validation_parent_job') != report['job_id']
                    or job.get('operation') not in record['binding']['operations']
                    or job.get('environment_digest') != record['environment_digest']):
                return False
            operations.add(job['operation'])
        if operations != set(record['binding']['operations']):
            return False
        return True
    except (OSError, KeyError, ValueError, TypeError):
        return False
