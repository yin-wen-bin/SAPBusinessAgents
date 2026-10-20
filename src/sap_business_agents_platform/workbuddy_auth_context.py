"""Credential-free WorkBuddy user/source and official-cache metadata observation."""
import hashlib
import json
import os
from pathlib import Path

ALLOWED = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "APPDATA",
           "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PATH"}


class AuthenticationContextError(ValueError):
    code = 'runtime_authentication_context_changed'

    def __init__(self):
        super().__init__(self.code)


def windows_user_paths():
    """Known-folder metadata for this token, not environment-supplied paths."""
    import ctypes
    from ctypes import wintypes as w
    import uuid
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise AuthenticationContextError()
    try:
        container, needed = w.DWORD(), w.DWORD()
        if not advapi.GetTokenInformation(token, 29, ctypes.byref(container), ctypes.sizeof(container), ctypes.byref(needed)) or container.value:
            raise AuthenticationContextError()  # Never escape an OS sandbox by rewriting paths.
    finally:
        kernel.CloseHandle(token)
    shell, ole = ctypes.WinDLL('shell32'), ctypes.WinDLL('ole32')
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    folders = {'USERPROFILE':'5E6C858F-0E22-4760-9AFE-EA3317B67173',
               'LOCALAPPDATA':'F1B32785-6FBA-4FCF-9D55-7B8E7F157091',
               'APPDATA':'3EB685DB-65F9-4CF6-A03A-E3EF65729F3D'}
    result = {}
    for name, identifier in folders.items():
        guid = ctypes.create_string_buffer(uuid.UUID(identifier).bytes_le, 16)
        pointer = ctypes.c_void_p()
        if shell.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(pointer)) != 0:
            raise AuthenticationContextError()
        try:
            result[name] = ctypes.wstring_at(pointer)
        finally:
            ole.CoTaskMemFree(pointer)
    return result


def validate_user_environment(environment, *, expected=None):
    if os.name != 'nt' and expected is None:
        return
    expected = windows_user_paths() if expected is None else expected
    values = {k.upper(): v for k, v in environment.items()}
    normalized = lambda value: os.path.normcase(os.path.normpath(value))
    if any(not values.get(k) or normalized(values[k]) != normalized(v) for k,v in expected.items()):
        raise AuthenticationContextError()


def clean_environment(environment):
    # Exactly the console login helper's user selection, not an inherited HOME
    # override. Never manufacture access to a different Windows user's profile.
    return {k: v for k, v in environment.items() if k.upper() in ALLOWED}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_signature(root):
    sources = {name: hashlib.sha256((Path(root) / 'scripts' / name).read_bytes()).hexdigest()
               for name in ('runtime-authentication-helper.py', 'login-workbuddy-runtime.py')}
    sources.update({name: hashlib.sha256((Path(root) / 'src/sap_business_agents_platform' / name).read_bytes()).hexdigest()
                    for name in ('workbuddy_auth_context.py', 'workbuddy_auth_signals.py', 'runtime_authentication_adapters.py')})
    return digest(sources)


def observe(root, environment, release_digest, *, external=False):
    values = {k.upper(): v for k, v in clean_environment(environment).items()}
    user = {k: str(Path(values[k])).casefold() for k in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA') if values.get(k)}
    sources = source_signature(root)
    context = digest({'user': user, 'sources': sources, 'release': release_digest, 'external': external})
    if external:
        return {'context_digest': context, 'cache_digest': None, 'source_digest': sources}
    base = values.get('LOCALAPPDATA')
    if not base:
        return {'context_digest': context, 'cache_digest': None, 'source_digest': sources}
    path = Path(base) / 'CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info'
    try:
        info = path.stat()  # Never open/decrypt/copy credential contents.
        stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except FileNotFoundError:
        stamp = 'missing'
    return {'context_digest': context, 'cache_digest': digest(stamp), 'source_digest': sources}
