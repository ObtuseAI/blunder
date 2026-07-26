from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence, TypedDict

from blunder.constitutional_adapters import record_validation_boundaries
from blunder.resource_accounting import ResourceMetric, measured, unmeasured


class BrokerResult(TypedDict):
    returncode: int
    output: str
    isolation: dict[str, object]
    telemetry: dict[str, ResourceMetric]


class BrokerTimeoutExpired(subprocess.TimeoutExpired):
    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: int,
        output: str,
        isolation: dict[str, object],
        telemetry: dict[str, ResourceMetric],
    ) -> None:
        super().__init__(list(command), timeout_seconds, output=output)
        self.isolation = isolation
        self.telemetry = telemetry


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobIoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount",
        "WriteOperationCount",
        "OtherOperationCount",
        "ReadTransferCount",
        "WriteTransferCount",
        "OtherTransferCount",
    )]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimit),
        ("IoInfo", _JobIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobBasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def unavailable_process_metrics(source: str, reason: str) -> dict[str, ResourceMetric]:
    return {
        "cpuTimeNanoseconds": unmeasured("ns", source, reason),
        "peakMemoryBytes": unmeasured("bytes", source, reason),
        "diskReadBytes": unmeasured("bytes", source, reason),
        "diskWriteBytes": unmeasured("bytes", source, reason),
    }


def query_windows_job_metrics(handle: int) -> dict[str, ResourceMetric]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    information = _JobExtendedLimit()
    returned_length = wintypes.DWORD()
    extended_succeeded = kernel32.QueryInformationJobObject(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
        ctypes.byref(returned_length),
    )
    if not extended_succeeded:
        return unavailable_process_metrics(
            "windows-job-object",
            f"QUERY_JOB_ACCOUNTING_FAILED_{ctypes.get_last_error()}",
        )
    accounting = _JobBasicAccounting()
    accounting_succeeded = kernel32.QueryInformationJobObject(
        handle,
        1,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        ctypes.byref(returned_length),
    )
    cpu = (
        measured(
            (int(accounting.TotalUserTime) + int(accounting.TotalKernelTime)) * 100,
            "ns",
            "windows-job-object",
        )
        if accounting_succeeded
        else unmeasured(
            "ns",
            "windows-job-object",
            f"QUERY_JOB_CPU_FAILED_{ctypes.get_last_error()}",
        )
    )
    return {
        "cpuTimeNanoseconds": cpu,
        "peakMemoryBytes": measured(int(information.PeakJobMemoryUsed), "bytes", "windows-job-object"),
        "diskReadBytes": measured(int(information.IoInfo.ReadTransferCount), "bytes", "windows-job-object"),
        "diskWriteBytes": measured(int(information.IoInfo.WriteTransferCount), "bytes", "windows-job-object"),
    }


def process_telemetry(
    job_handle: int | None,
    started_monotonic_ns: int,
) -> dict[str, ResourceMetric]:
    if job_handle is not None:
        telemetry = query_windows_job_metrics(job_handle)
    elif os.name == "nt":
        telemetry = unavailable_process_metrics(
            "windows-parent-job-boundary",
            "WINDOWS_JOB_OBJECT_BLOCKED_BY_PARENT_POLICY",
        )
    else:
        telemetry = unavailable_process_metrics(
            "portable-process-boundary",
            "PLATFORM_CHILD_COUNTER_UNSUPPORTED",
        )
    telemetry["processDurationNanoseconds"] = measured(
        time.monotonic_ns() - started_monotonic_ns,
        "ns",
        "validation-broker-monotonic-clock",
    )
    return telemetry


def windows_job_handle(process_id: int, memory_bytes: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    information = ExtendedLimit()
    information.BasicLimitInformation.LimitFlags = 0x00002000 | 0x00000008 | 0x00000100
    information.BasicLimitInformation.ActiveProcessLimit = 1
    information.ProcessMemoryLimit = memory_bytes
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(information), ctypes.sizeof(information)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    process = kernel32.OpenProcess(0x0100 | 0x0400, False, process_id)
    if not process:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "OpenProcess failed")
    try:
        if not kernel32.AssignProcessToJobObject(job, process):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise OSError(error, "AssignProcessToJobObject failed")
    finally:
        kernel32.CloseHandle(process)
    return int(job)


def close_windows_handle(handle: int) -> None:
    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def brokered_run(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    memory_bytes: int,
) -> BrokerResult:
    started_monotonic_ns = time.monotonic_ns()
    constitutional_decision = record_validation_boundaries(
        cwd,
        command,
        datetime.now(timezone.utc),
    )
    isolation: dict[str, object] = {
        "platform": os.name,
        "kernelProcessLimit": False,
        "processLimit": 1,
        "memoryLimitBytes": memory_bytes,
        "killOnBrokerClose": False,
        "constitutionalDecision": constitutional_decision,
    }
    base_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    breakaway_flags = base_flags | (0x01000000 if os.name == "nt" else 0)
    launch_arguments = {
        "cwd": cwd,
        "env": dict(environment),
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    try:
        process = subprocess.Popen(list(command), creationflags=breakaway_flags, **launch_arguments)
        isolation["launchBoundary"] = "breakaway-requested" if os.name == "nt" else "portable"
    except PermissionError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 5:
            raise
        process = subprocess.Popen(list(command), creationflags=base_flags, **launch_arguments)
        isolation["launchBoundary"] = "parent-job-inherited"
    job_handle: int | None = None
    telemetry: dict[str, ResourceMetric]
    try:
        if os.name == "nt":
            try:
                job_handle = windows_job_handle(process.pid, memory_bytes)
            except OSError as error:
                if error.errno != 5:
                    process.kill()
                    process.communicate()
                    raise
                isolation["broker"] = "windows-parent-job-boundary"
                isolation["jobObjectStatus"] = "BLOCKED_BY_PARENT_JOB_POLICY"
            else:
                isolation["broker"] = "windows-job-object"
                isolation["kernelProcessLimit"] = True
                isolation["killOnBrokerClose"] = True
        else:
            isolation["broker"] = "portable-process-boundary"
        output, _ = process.communicate(timeout=timeout_seconds)
        telemetry = process_telemetry(job_handle, started_monotonic_ns)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        telemetry = process_telemetry(job_handle, started_monotonic_ns)
        raise BrokerTimeoutExpired(command, timeout_seconds, output, isolation, telemetry)
    finally:
        if job_handle is not None:
            close_windows_handle(job_handle)
    return {
        "returncode": process.returncode,
        "output": output,
        "isolation": isolation,
        "telemetry": telemetry,
    }
