"""Real Windows reader/writer sharing, entirely local; no SDK/model/SAP."""
import json
import os
import threading

import pytest

from sap_business_agents_platform.workbuddy_environment import atomic_json
from sap_business_agents_platform.workbuddy_record_io import open_record_snapshot, read_record


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics")
def test_ordinary_reader_reproduces_replace_denial_without_changing_acl(tmp_path):
    target = tmp_path / "journal.json"
    atomic_json(target, {"sequence": 0})
    with target.open("rb"):
        with pytest.raises(PermissionError) as failure:
            atomic_json(target, {"sequence": 1})
    assert failure.value.winerror in {5, 32, 33}
    assert read_record(target) == {"sequence": 0}
    assert not list(tmp_path.glob("*.tmp"))


def test_reader_keeps_consistent_snapshot_and_writer_waits_for_handle_close(tmp_path):
    target = tmp_path / "journal.json"
    atomic_json(target, {"sequence": 0})
    entered, finished, errors = threading.Event(), threading.Event(), []
    def writer():
        entered.set()
        try:
            atomic_json(target, {"sequence": 1})
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()
    with open_record_snapshot(target) as old:
        thread = threading.Thread(target=writer)
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(.03)
        assert json.loads(old.read()) == {"sequence": 0}
    thread.join(2)
    assert finished.is_set() and not errors
    assert read_record(target) == {"sequence": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_journal_snapshots_are_complete_and_do_not_deny_writes(tmp_path):
    target = tmp_path / "journal.json"
    atomic_json(target, {"sequence": 0})
    stop, errors = threading.Event(), []
    def reader():
        try:
            while not stop.is_set():
                assert type(read_record(target)["sequence"]) is int
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for sequence in range(1, 100):
            atomic_json(target, {"sequence": sequence})
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    assert read_record(target) == {"sequence": 99}


def test_reader_never_treats_missing_or_corrupt_record_as_empty(tmp_path):
    target = tmp_path / "journal.json"
    with pytest.raises(FileNotFoundError):
        read_record(target)
    target.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_record(target)


def test_immutable_installation_inventory_never_receives_a_lock_file(tmp_path):
    directory = tmp_path / 'releases' / ('a' * 64)
    target = directory / 'environment.json'
    atomic_json(target, {"files": {}})
    assert read_record(target) == {"files": {}}
    assert [p.name for p in directory.iterdir()] == ['environment.json']


def test_lock_wait_cannot_extend_the_signed_deadline(tmp_path):
    import time
    target = tmp_path / 'journal.json'
    atomic_json(target, {"sequence": 0})
    errors = []
    def writer():
        try:
            atomic_json(target, {"sequence": 1}, deadline=time.monotonic() + .02)
        except OSError as exc:
            errors.append(exc)
    with open_record_snapshot(target):
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(.2)
        assert not thread.is_alive()
    assert len(errors) == 1 and read_record(target) == {"sequence": 0}
