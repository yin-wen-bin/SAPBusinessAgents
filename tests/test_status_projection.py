import threading
import time

from sap_business_agents_platform.status_projection import StatusProjection


def until(predicate):
    limit = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < limit
        time.sleep(.005)


def test_slow_provider_never_blocks_display_or_other_provider_and_only_one_refresh():
    entered, release = threading.Event(), threading.Event()
    count = []
    def slow():
        count.append(1); entered.set(); release.wait(2)
        return {"can_enable": True, "blockers": []}
    cache = StatusProjection({"slow": slow, "fast": lambda: {"can_enable": True}},
        {"slow": lambda: 1, "fast": lambda: 1}, {k: lambda: {} for k in ("slow", "fast")}, refresh_limit=.02)
    start = time.monotonic()
    assert not cache.get("slow")["can_enable"]
    assert time.monotonic() - start < .1
    assert entered.wait(1)
    until(lambda: cache.get("fast")["status_refresh"]["status"] == "ready")
    time.sleep(.03)
    for _ in range(50):
        result = cache.get("slow")
        assert result["status_refresh"]["error_code"] == "status_refresh_timeout"
        assert not result["can_enable"]
    assert count == [1]
    release.set()
    until(lambda: not cache._slots["slow"]["running"])
    assert cache.get("slow")["status_refresh"]["status"] == "failed"
    cache.close()


def test_get_only_enqueues_and_never_creates_a_thread(monkeypatch):
    release = threading.Event()
    cache = StatusProjection({'a': lambda: release.wait(2) and {'can_enable': True}},
        {'a': lambda: 1}, {'a': lambda: {}})
    def forbidden(*args, **kwargs):
        raise AssertionError('thread creation on GET')
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    assert cache.get('a')['can_enable'] is False
    release.set()
    cache.close()


def test_active_control_reader_refreshes_between_sparse_gets_and_stops_after_idle():
    count = []
    now = [0.]
    cache = StatusProjection({'control': lambda: count.append(1) or {'enabled': True}},
        {'control': lambda: 1}, {'control': lambda: {}}, ttl=5, clock=lambda: now[0],
        refresh_every=.01, idle_after=30)
    cache.get('control')
    until(lambda: cache._slots['control'].get('value') is not None)
    for instant in (4., 8., 12.):
        previous = len(count); now[0] = instant
        until(lambda: len(count) > previous)
    # No GET for twelve seconds: the actual five-second projection is still
    # fresh, not a stale cache silently relabelled current.
    assert cache.get('control')['status_refresh']['stale'] is False
    now[0] = 43.
    time.sleep(.03)
    previous = len(count)
    time.sleep(.03)
    assert len(count) == previous
    cache.close()


def test_late_binding_result_discarded_and_stale_never_grants_enable():
    release = threading.Event()
    binding = [1]
    cache = StatusProjection({"a": lambda: (release.wait(2) or True) and {"can_enable": True}},
        {"a": lambda: binding[0]}, {"a": lambda: {}}, ttl=.02)
    cache.get("a"); binding[0] = 2; release.set()
    until(lambda: not cache._slots["a"]["running"])
    assert cache.get("a")["status_refresh"]["error_code"] == "status_binding_changed"
    assert not cache.get("a")["can_enable"]
    cache.close()


def test_cache_max_sixty_seconds_and_error_is_safe():
    now = [0.]
    cache = StatusProjection({"a": lambda: {"can_enable": True}}, {"a": lambda: 1},
        {"a": lambda: {}}, clock=lambda: now[0])
    cache.get("a"); until(lambda: cache.get("a")["status_refresh"]["status"] == "ready")
    cache.close(); now[0] = 61
    assert not cache.get("a")["can_enable"]
    assert cache.get("a")["status_refresh"]["stale"]
    broken = StatusProjection({"a": lambda: (_ for _ in ()).throw(ValueError("SECRET"))},
        {"a": lambda: 1}, {"a": lambda: {}})
    broken.get("a"); until(lambda: not broken._slots["a"]["running"])
    assert "SECRET" not in str(broken.get("a"))
    assert broken.get("a")["status_refresh"]["error_code"] == "status_projection_failed"
    broken.close()


def test_batch_operation_digests_keep_original_algorithm_and_read_files_once(monkeypatch):
    import hashlib
    import json
    from pathlib import Path
    from sap_business_agents_platform import runtime_policy as policy
    reads, original = [], Path.read_bytes
    def read(path):
        reads.append(path.name)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    actual = policy.orchestration_digests()
    assert sorted(reads) == sorted(policy.ORCHESTRATION_FILES)
    files = {name: hashlib.sha256(original(Path(policy.__file__).parent / name).replace(b"\r\n", b"\n")).hexdigest()
             for name in policy.ORCHESTRATION_FILES}
    expected = {op: hashlib.sha256(json.dumps({"version": policy.ORCHESTRATION_VERSION,
        "operation": op, "files": files}, sort_keys=True).encode()).hexdigest() for op in policy.OPERATIONS}
    assert actual == expected


def test_slow_guard_and_invalidated_refresh_do_not_block_or_reuse_result():
    release = threading.Event()
    cache = StatusProjection({"a": lambda: {"can_enable": True}}, {"a": lambda: 1},
        {"a": lambda: {}}, guards={"a": lambda: release.wait(2) or 1})
    start = time.monotonic()
    assert not cache.get("a")["can_enable"]
    assert time.monotonic() - start < .1
    cache.invalidate()
    release.set()
    until(lambda: not cache._slots["a"]["running"])
    assert not cache.get("a")["can_enable"]
    assert cache.get("a")["status_refresh"]["error_code"] == "status_binding_changed"
    cache.close()


def test_opted_in_provider_can_complete_late_with_unchanged_binding():
    release, entered = threading.Event(), threading.Event()
    now = [0.]
    def slow():
        entered.set()
        release.wait(2)
        return {'can_enable': True, 'blockers': []}
    cache = StatusProjection({'workbuddy': slow}, {'workbuddy': lambda: 1},
        {'workbuddy': lambda: {}}, clock=lambda: now[0], refresh_limit=10,
        accept_late={'workbuddy'}, guards={'workbuddy': lambda: 'same'})
    assert not cache.get('workbuddy')['can_enable']
    assert entered.wait(1)
    now[0] = 11
    assert cache.get('workbuddy')['status_refresh']['error_code'] == 'status_refresh_timeout'
    assert cache.get('workbuddy')['status_refresh']['running'] is True
    assert cache.get('workbuddy')['status_refresh']['wait_exceeded'] is True
    assert cache.get('workbuddy')['status_refresh']['status'] == 'refreshing'
    assert not cache.get('workbuddy')['can_enable']
    release.set()
    until(lambda: not cache._slots['workbuddy']['running'])
    value = cache.get('workbuddy')
    assert value['can_enable'] and value['status_refresh']['status'] == 'ready'
    assert value['status_refresh']['completed_after_limit'] is True
    cache.close()
    now[0] = 72
    assert not cache.get('workbuddy')['can_enable']


def test_opted_in_late_result_still_rejects_guard_change_and_close():
    for invalidate in ('guard', 'closed'):
        release, entered = threading.Event(), threading.Event()
        guard, now = [1], [0.]
        def slow():
            entered.set()
            release.wait(2)
            return {'can_enable': True}
        cache = StatusProjection({'workbuddy': slow}, {'workbuddy': lambda: 1},
            {'workbuddy': lambda: {}}, clock=lambda: now[0], refresh_limit=10,
            accept_late={'workbuddy'}, guards={'workbuddy': lambda: guard[0]})
        cache.get('workbuddy')
        assert entered.wait(1)
        now[0] = 11
        if invalidate == 'guard':
            guard[0] = 2
        else:
            cache.close()
        release.set()
        until(lambda: not cache._slots['workbuddy']['running'])
        assert not cache.get('workbuddy')['can_enable']
        cache.close()
