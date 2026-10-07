from pathlib import Path
import runpy
import subprocess
import sys

import pytest


def _entry():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/prepush.py'))['main']


def test_prepush_stops_after_failed_documentation(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(sys, 'argv', ['prepush.py'])
    monkeypatch.setattr(entry.__globals__['importlib'].util, 'find_spec', lambda name: object())
    monkeypatch.setattr(entry.__globals__['shutil'], 'which', lambda name: name)
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(subprocess, 'run', fail)
    assert entry() == 17
    assert len(calls) == 1
    assert 'scripts/documentation.py' in calls[0]


def test_prepush_requires_sdk_free_environment_when_requested(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(sys, 'argv', ['prepush.py', '--scope', 'python', '--require-no-workbuddy'])
    monkeypatch.setattr(entry.__globals__['importlib'].util, 'find_spec', lambda name: object())

    def unexpected(*args, **kwargs):
        pytest.fail('No validation should start in a contaminated clean-CI environment.')

    monkeypatch.setattr(subprocess, 'run', unexpected)
    with pytest.raises(SystemExit) as exc:
        entry()
    assert exc.value.code == 2


def test_prepush_propagates_site_audit_or_test_failure(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(sys, 'argv', ['prepush.py', '--scope', 'site'])
    monkeypatch.setattr(entry.__globals__['shutil'], 'which', lambda name: name)
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 1))
    assert entry() == 1
