"""Owned CLI terminal trace fallback for a silent SDK. Never scrape account files."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import time
from itertools import islice

from .workbuddy_auth_signals import authentication_failure, HTTP_401
from .workbuddy_job import process_identity


def prompt_matches(value, expected, depth=0):
    if depth > 16:
        return False
    if isinstance(value, str):
        if value.startswith('<user_query>') and value.endswith('</user_query>'):
            value = value[12:-13]
        raw = value.encode('utf-8')
        return len(raw) == expected.get('input_length') and hashlib.sha256(raw).hexdigest() == expected.get('input_sha256')
    if isinstance(value, dict):
        return any(prompt_matches(v, expected, depth + 1) for v in value.values())
    if isinstance(value, list):
        return any(prompt_matches(v, expected, depth + 1) for v in value[:64])
    return False


def trace_failure(value, *, pid, expected, launched_at):
    header = value.get('trace') if isinstance(value, dict) else None
    spans = value.get('spans') if isinstance(value, dict) else None
    if not isinstance(header, dict) or not isinstance(spans, list) or len(spans) > 256:
        return None
    if type(header.get('workerPid')) is not int or header['workerPid'] != pid or header.get('status') != 'error':
        return None
    try:
        start_date = datetime.fromisoformat(header['startedAt'].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(header['endedAt'].replace('Z', '+00:00'))
        if start_date.tzinfo is None or end_date.tzinfo is None:
            return None
        start, end = start_date.timestamp(), end_date.timestamp()
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if start < launched_at or end < start or end > time.time() + 5:
        return None
    trace_id = header.get('traceId')
    if not isinstance(trace_id, str) or not trace_id or len(trace_id) > 128:
        return None
    indexed = {}
    for span in spans:
        if not isinstance(span, dict) or span.get('traceId') != trace_id:
            return None
        identifier = span.get('spanId')
        if not isinstance(identifier, str) or not identifier or identifier in indexed:
            return None
        indexed[identifier] = span
    matched = []
    for span in spans:
        # An input span may have completed successfully while its parent agent
        # run failed later. Bind the *request*, then walk only its own ancestry;
        # requiring the input span itself to be erroneous misses that terminal.
        try:
            tool_input = json.loads(span.get('toolInput', 'null'))
        except (ValueError, TypeError):
            continue
        if prompt_matches(tool_input, expected):
            current, visited = span, set()
            for _ in range(16):
                if current['spanId'] in visited:
                    break
                visited.add(current['spanId'])
                if current.get('status') == 'error':
                    matched.append(current)
                    break
                current = indexed.get(current.get('parentId'))
                if current is None:
                    break
    if not matched:
        return None  # No task/prompt binding: never act on another job's trace.
    # The owned request's failed ancestor may contain only a generic CLI error.
    # Inspect errors in that ancestor's subtree, not other jobs or unrelated traces.
    roots = {span['spanId'] for span in matched}
    related = list(matched)
    for span in spans:
        if not span.get('error'): continue
        current, visited = span, set()
        for _ in range(16):
            if current['spanId'] in visited: break
            visited.add(current['spanId'])
            if current['spanId'] in roots:
                if span not in related: related.append(span)
                break
            current = indexed.get(current.get('parentId'))
            if current is None: break
    code = None
    for span in related:
        candidate = authentication_failure(span.get('error'))
        if candidate:
            code = candidate
            break
    from .runtime_diagnostics import safe_upstream_diagnostic
    observed = [safe_upstream_diagnostic(span.get('error')) for span in related]
    selected = max(observed, key=lambda d: (d.get('upstream_category') != 'unknown',
        'upstream_http_status' in d, 'upstream_phase' in d, 'upstream_request_id_sha256' in d))
    return {'failure_code': code or 'workbuddy_upstream_error', 'failure_source': 'owned_cli_trace',
            **selected,
            'trace_terminal_error': True,
            **({'http_status': 401} if code == 'workbuddy_authentication_required'
               and any(HTTP_401.search(str(span.get('error') or '')) for span in related) else {}),
            **({'authentication_reason':'login_required'} if code == 'workbuddy_authentication_required'
               and not any(HTTP_401.search(str(span.get('error') or '')) for span in related) else {})}


def early_authentication_failure(value, *, pid, workspace, launched_at, log_root, budget=None):
    """Pinned CLI early-init failure, bound by owned PID, trace and workspace.

    Never weakens the prompt-based trace route. Only traces without ANY input
    are eligible, and only the CLI's exact error record can supply a reason.
    No log text, account data, URLs or prompt leaves this function.
    """
    if not workspace or not isinstance(value, dict):
        return None
    header, spans = value.get('trace'), value.get('spans')
    if (not isinstance(header, dict) or type(header.get('workerPid')) is not int
            or header['workerPid'] != pid or header.get('status') != 'error'
            or not isinstance(spans, list) or not 0 < len(spans) <= 256
            or any(not isinstance(s, dict) or 'toolInput' in s for s in spans)):
        return None
    trace_id = header.get('traceId')
    if (not isinstance(trace_id, str) or not trace_id or len(trace_id) > 128
            or any(s.get('traceId') != trace_id for s in spans)
            or not any(s.get('parentId') is None and s.get('status') == 'error'
                       and s.get('error') == 'Error in agent run' for s in spans)):
        return None
    try:
        start = datetime.fromisoformat(header['startedAt'].replace('Z','+00:00'))
        end = datetime.fromisoformat(header['endedAt'].replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None or not launched_at <= start.timestamp() <= end.timestamp() <= time.time()+5:
            return None
        directory = Path(log_root) / start.astimezone().strftime('%Y-%m-%d')
        if Path(log_root).is_symlink() or directory.is_symlink() or directory.resolve().parent != Path(log_root).resolve():
            return None
        entries = list(islice(directory.iterdir(), 65))
        if len(entries) > 64:
            return None
        budget = budget if budget is not None else {'bytes':8 * 1024 * 1024,'files':8}
        paths = sorted((p for p in entries if p.is_file() and p.suffix == '.log'
                        and p.name.startswith(Path(workspace).name+'__')),
                       key=lambda p:p.stat().st_mtime_ns, reverse=True)[:8]
        for path in paths:
            info = path.stat()
            if (path.is_symlink() or path.resolve().parent != directory.resolve()
                    or info.st_mtime < launched_at or info.st_size > budget['bytes'] or budget['files'] <= 0):
                continue
            with path.open('rb') as stream:
                raw = stream.read(min(budget['bytes'], 8 * 1024 * 1024)+1)
            budget['bytes'] -= len(raw)
            budget['files'] -= 1
            if budget['bytes'] < 0:
                return None
            lines = raw.decode('utf-8',errors='replace').splitlines()
            prefix = 'Workspace Path: '
            if not lines or not lines[0].startswith(prefix):
                continue
            observed = lines[0][len(prefix):].strip()
            if os.path.normcase(os.path.abspath(observed)) != os.path.normcase(os.path.abspath(workspace)):
                continue
            for line in lines[1:]:
                match = re.fullmatch(r'\[([^\]]+)\] \[Error\] \[pid=(\d+)\] \[e: (Authentication required\. Please use /login command to sign in to your account)\]  Failed to handle run result:',line)
                if not match or int(match[2]) != pid:
                    continue
                at = datetime.strptime(match[1],'%Y/%m/%d %H:%M:%S.%f').astimezone().timestamp()
                if start.timestamp() <= at <= end.timestamp()+5 and at >= launched_at:
                    return {'failure_code':'workbuddy_authentication_required',
                            'failure_source':'owned_cli_trace','authentication_reason':'login_required',
                            'trace_terminal_error':True}
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        pass
    return None


class OwnedFailureWatch:
    def __init__(self, job, cli_path, expected, launched_at, *, root=None, workspace=None):
        self.job, self.cli_path, self.expected, self.launched_at = job, cli_path, expected, launched_at
        self.root = root or Path(os.environ.get('USERPROFILE') or Path.home()) / '.codebuddy/traces'
        self.identities, self.seen = {}, {}
        self.workspace = workspace

    def poll(self):
        try:
            for pid, identity in self.job.cli_processes(self.cli_path).items():
                self.identities.setdefault(pid, identity)  # Never rebind an observed PID to a new birth.
        except (OSError, AttributeError):
            return None
        remaining_bytes, remaining_files = 8 * 1024 * 1024, 16
        for pid, identity in list(self.identities.items())[:16]:
            current = process_identity(pid)
            if current == 'unknown' or current is not None and current != identity:
                continue  # PID reuse/access uncertainty is not ownership.
            directory = self.root / str(pid)
            try:
                if directory.is_symlink() or directory.resolve().parent != self.root.resolve():
                    continue
                entries = list(islice(directory.iterdir(), 65))
                if len(entries) > 64:
                    continue  # Bound even a reused PID's historical directory.
                paths = sorted((p for p in entries if p.name.startswith('trace_') and p.suffix == '.json'),
                               key=lambda p: p.stat().st_mtime_ns, reverse=True)[:16]
                for path in paths:
                    if path.resolve().parent != directory.resolve() or path.is_symlink():
                        continue
                    info = path.stat()
                    if info.st_size > remaining_bytes or remaining_files <= 0 or info.st_mtime < self.launched_at:
                        continue
                    marker = (info.st_size, info.st_mtime_ns)
                    if self.seen.get(path) == marker:
                        continue
                    with path.open('rb') as stream:
                        raw = stream.read(8 * 1024 * 1024 + 1)
                    remaining_bytes -= len(raw)
                    remaining_files -= 1
                    if len(raw) > 8 * 1024 * 1024:
                        continue
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        self.seen[path] = marker
                        continue
                    failure = trace_failure(value, pid=pid, expected=self.expected, launched_at=self.launched_at)
                    if failure is None and self.workspace:
                        log_budget = {'bytes':remaining_bytes,'files':remaining_files}
                        failure = early_authentication_failure(value, pid=pid, workspace=self.workspace,
                            launched_at=self.launched_at, log_root=self.root.parent/'logs', budget=log_budget)
                        remaining_bytes,remaining_files = log_budget['bytes'],log_budget['files']
                    if failure:
                        return failure
                    # The trace may be flushed just before its paired CLI log.
                    # Retry only early error traces; don't permanently miss that
                    # ordered write race while the trace itself is unchanged.
                    if not (isinstance(value.get('trace'),dict) and value['trace'].get('status') == 'error'
                            and self.workspace and all('toolInput' not in s for s in value.get('spans',[]) if isinstance(s,dict))):
                        self.seen[path] = marker
            except (OSError, ValueError, TypeError):
                continue  # Partial/unknown traces cannot invent an error or success.
        return None

    async def wait(self):
        while True:
            value = await asyncio.to_thread(self.poll)
            if value:
                return value
            await asyncio.sleep(1)
