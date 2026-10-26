"""Offline archive publishing regressions; never start a service."""
import importlib.util
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive_launcher', ROOT/'scripts/service-startup.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_transient_rename_only(tmp_path, monkeypatch, winerror):
    staged = tmp_path/'stage'; staged.mkdir(); (staged/'proof').write_text('immutable')
    target = tmp_path/'published'; original = Path.rename; calls = []; delays = []
    def rename(path, destination):
        calls.append(path)
        if len(calls) < 3:
            error = PermissionError('held'); error.winerror = winerror; raise error
        return original(path, destination)
    monkeypatch.setattr(Path, 'rename', rename)
    monkeypatch.setattr(launcher.time, 'sleep', delays.append)
    launcher.publish_archive(staged, target)
    assert calls == [staged]*3 and delays == [.1, .2]
    assert (target/'proof').read_text() == 'immutable'


def test_persistent_denial_is_bounded_and_keeps_stage(tmp_path, monkeypatch):
    staged = tmp_path/'stage'; staged.mkdir(); calls = []; delays = []
    def rename(path, destination):
        calls.append(path)
        error = PermissionError('held'); error.winerror = 5; raise error
    monkeypatch.setattr(Path, 'rename', rename)
    monkeypatch.setattr(launcher.time, 'sleep', delays.append)
    with pytest.raises(launcher.control.StartupBlocked, match='startup_archive_publish_busy'):
        launcher.publish_archive(staged, tmp_path/'published')
    assert len(calls) == 5 and delays == [.1, .2, .4, .8]
    assert staged.is_dir() and not (tmp_path/'published').exists()


def test_conflict_never_overwritten(tmp_path):
    staged = tmp_path/'stage'; staged.mkdir()
    target = tmp_path/'published'; target.mkdir(); (target/'proof').write_text('original')
    with pytest.raises(launcher.control.StartupBlocked, match='startup_archive_target_conflict'):
        launcher.publish_archive(staged, target)
    assert staged.is_dir() and (target/'proof').read_text() == 'original'


def test_other_io_error_not_retried(tmp_path, monkeypatch):
    staged = tmp_path/'stage'; staged.mkdir()
    error = OSError('disk failure'); error.winerror = 112
    def rename(*args): raise error
    monkeypatch.setattr(Path, 'rename', rename)
    monkeypatch.setattr(launcher.time, 'sleep', lambda _: pytest.fail('unexpected retry'))
    with pytest.raises(OSError) as caught:
        launcher.publish_archive(staged, tmp_path/'published')
    assert caught.value is error and staged.exists()


def test_busy_publish_has_actionable_cli_message(monkeypatch, capsys):
    def blocked(_):
        raise launcher.control.StartupBlocked('startup_archive_publish_busy')
    monkeypatch.setattr(launcher, 'ensure', blocked)
    assert launcher.main(['ensure', '--no-browser']) == 1
    message = capsys.readouterr().err
    assert 'bounded retries' in message and 'no service was switched' in message
    assert 'Do not disable security software' in message and 'Traceback' not in message


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory sharing contract')
def test_real_windows_directory_handle_is_released_between_retries(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]; kernel.CloseHandle.restype = w.BOOL
    staged = tmp_path/'stage'; staged.mkdir(); (staged/'proof').write_text('immutable')
    # Deliberately omit FILE_SHARE_DELETE, reproducing scanner/editor handles.
    handle = kernel.CreateFileW(str(staged), 0x80000000, 3, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value
    released = False; delays = []
    def release(delay):
        nonlocal released
        delays.append(delay)
        assert kernel.CloseHandle(handle)
        released = True
    monkeypatch.setattr(launcher.time, 'sleep', release)
    try:
        launcher.publish_archive(staged, tmp_path/'published')
        assert released and delays == [.1]
        assert (tmp_path/'published/proof').read_text() == 'immutable'
    finally:
        if not released: kernel.CloseHandle(handle)
