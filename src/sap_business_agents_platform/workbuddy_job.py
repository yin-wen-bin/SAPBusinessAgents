"""Owned Windows Job Object; never terminate a process solely by PID."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w


class WindowsJob:
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.SetInformationJobObject.restype = w.BOOL
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.AssignProcessToJobObject.restype = w.BOOL
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        kernel.TerminateJobObject.restype = w.BOOL
        kernel.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype = w.BOOL
        self.kernel = kernel
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("processes", w.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")

    def attach(self, pid: int) -> None:
        if self.handle is None:
            return
        process = self.kernel.OpenProcess(0x0100 | 0x0001 | 0x0400, False, pid)
        if not process:
            raise OSError(ctypes.get_last_error(), "OpenProcess")
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        finally:
            self.kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def terminate(self) -> None:
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
            raise OSError(ctypes.get_last_error(), "TerminateJobObject")

    def empty(self) -> bool:
        if self.handle is None:
            return True
        class Accounting(ctypes.Structure):
            _fields_ = [("total_user", ctypes.c_int64), ("total_kernel", ctypes.c_int64),
                        ("period_user", ctypes.c_int64), ("period_kernel", ctypes.c_int64),
                        ("faults", w.DWORD), ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]
        value = Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None):
            return False
        return value.active == 0


def process_identity(pid: int) -> str | None:
    """Creation time, not process name, protects reconciliation against PID reuse."""
    if os.name != "nt":
        from pathlib import Path
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return None if fields[0] == "Z" else fields[19]
        except (OSError, IndexError):
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        # Access denied cannot be treated as absence.
        if ctypes.get_last_error() == 5:
            return "unknown"
        return None
    values = [w.FILETIME() for _ in range(4)]
    try:
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return "unknown"
        return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)
