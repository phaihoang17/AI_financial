"""Windows worker limits through a Job Object (ADR-072).

Windows has no ``setrlimit``. The worker puts itself in a new job object
before it reads untrusted input, mirroring ``apply_worker_resource_limits`` on
POSIX: per-process committed memory, per-process user CPU time, one active
process (no children), kill-on-close, no error dialogs and no desktop, clipboard,
handle or system-setting access. The job handle is kept for the worker's
lifetime; when the worker exits the handle closes and the job kills anything
left in it.

Network and file isolation on Windows rely on the in-process audit guards; a
production Windows host runs the Linux worker in the ``container-v1`` mode.
"""

from __future__ import annotations

import ctypes
from typing import Any

# Win32 DWORD is 32-bit; ctypes.wintypes.DWORD is c_ulong, 64-bit off Windows,
# so the layout is spelled out to stay identical (and testable) on every host.
DWORD = ctypes.c_uint32

JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_UILIMIT_ALL = 0x000000FF  # handles, read/write clipboard, system params, display, global atoms, desktop, exit
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_BASIC_UI_RESTRICTIONS_CLASS = 4
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002
_TICKS_PER_SECOND = 10_000_000  # 100-nanosecond units


class IO_COUNTERS(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):  # noqa: N801
    _fields_ = [("UIRestrictionsClass", DWORD)]


def job_limit_information(profile: Any) -> JOBOBJECT_EXTENDED_LIMIT_INFORMATION:
    """The extended limits of one ``SandboxLimitsProfile``."""
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    basic = info.BasicLimitInformation
    basic.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_TIME | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                        | JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                        | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    basic.PerProcessUserTimeLimit = profile.cpu_hard_seconds * _TICKS_PER_SECOND
    basic.ActiveProcessLimit = profile.max_processes
    info.ProcessMemoryLimit = profile.memory_bytes
    return info


_job_handle: int | None = None


def apply_job_limits(profile: Any, kernel32: Any = None) -> int:
    """Create the worker's job, set its limits and assign the current process to it."""
    global _job_handle
    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObjectW failed")
    info = job_limit_information(profile)
    if not kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError("SetInformationJobObject(extended limits) failed")
    ui = JOBOBJECT_BASIC_UI_RESTRICTIONS(JOB_OBJECT_UILIMIT_ALL)
    if not kernel32.SetInformationJobObject(job, JOB_OBJECT_BASIC_UI_RESTRICTIONS_CLASS,
                                            ctypes.byref(ui), ctypes.sizeof(ui)):
        raise OSError("SetInformationJobObject(UI restrictions) failed")
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        raise OSError("AssignProcessToJobObject failed")
    _job_handle = job
    return job
