"""Read-only resource sampling and explicit ResourceCharge contracts."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from datetime import datetime
from typing import Mapping, TypedDict

from blunder.chronos import ChronosContext, chronos_now
from blunder.constitutional_kernel import canonical_sha256


RESOURCE_CHARGE_SCHEMA = "obtuse.blunder.resource-charge.v1"
RESOURCE_METRIC_NAMES: tuple[str, ...] = (
    "processDurationNanoseconds",
    "cpuTimeNanoseconds",
    "peakMemoryBytes",
    "diskReadBytes",
    "diskWriteBytes",
    "queueDepth",
    "queueCapacity",
    "providerCalls",
    "providerTokens",
    "providerCostUsd",
)


class ResourceMetric(TypedDict):
    state: str
    value: int | float | None
    unit: str
    source: str
    reason: str | None


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount",
        "WriteOperationCount",
        "OtherOperationCount",
        "ReadTransferCount",
        "WriteTransferCount",
        "OtherTransferCount",
    )]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def measured(value: int | float, unit: str, source: str) -> ResourceMetric:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"Measured resource value must be non-negative: {value}")
    if not unit or not source:
        raise ValueError("Measured resource values require unit and source.")
    return {"state": "MEASURED", "value": value, "unit": unit, "source": source, "reason": None}


def unmeasured(unit: str, source: str, reason: str) -> ResourceMetric:
    if not unit or not source or not reason:
        raise ValueError("Unmeasured resource values require unit, source, and reason.")
    return {"state": "UNMEASURED", "value": None, "unit": unit, "source": source, "reason": reason}


def _filetime_ticks(value: wintypes.FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _windows_process_metrics() -> dict[str, ResourceMetric]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    process = kernel32.GetCurrentProcess()
    created = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    kernel32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
        cpu = unmeasured("ns", "windows-process-api", f"GET_PROCESS_TIMES_FAILED_{ctypes.get_last_error()}")
    else:
        cpu = measured((_filetime_ticks(kernel) + _filetime_ticks(user)) * 100, "ns", "windows-process-api")
    memory = _ProcessMemoryCounters()
    memory.cb = ctypes.sizeof(memory)
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
        peak_memory = unmeasured("bytes", "windows-process-api", f"GET_PROCESS_MEMORY_FAILED_{ctypes.get_last_error()}")
    else:
        peak_memory = measured(int(memory.PeakWorkingSetSize), "bytes", "windows-process-api")
    io = _IoCounters()
    kernel32.GetProcessIoCounters.argtypes = (wintypes.HANDLE, ctypes.POINTER(_IoCounters))
    kernel32.GetProcessIoCounters.restype = wintypes.BOOL
    if not kernel32.GetProcessIoCounters(process, ctypes.byref(io)):
        reason = f"GET_PROCESS_IO_FAILED_{ctypes.get_last_error()}"
        disk_read = unmeasured("bytes", "windows-process-api", reason)
        disk_write = unmeasured("bytes", "windows-process-api", reason)
    else:
        disk_read = measured(int(io.ReadTransferCount), "bytes", "windows-process-api")
        disk_write = measured(int(io.WriteTransferCount), "bytes", "windows-process-api")
    return {
        "cpuTimeNanoseconds": cpu,
        "peakMemoryBytes": peak_memory,
        "diskReadBytes": disk_read,
        "diskWriteBytes": disk_write,
    }


def sample_local_process(started_monotonic_ns: int) -> dict[str, ResourceMetric]:
    """Read the current process without changing process or operating-system state."""

    observed_monotonic_ns = time.monotonic_ns()
    if started_monotonic_ns > observed_monotonic_ns:
        raise ValueError("Resource sampler start time is in the future.")
    metrics: dict[str, ResourceMetric] = {
        "processDurationNanoseconds": measured(
            observed_monotonic_ns - started_monotonic_ns,
            "ns",
            "monotonic-clock",
        )
    }
    if os.name == "nt":
        metrics.update(_windows_process_metrics())
    else:
        metrics.update(
            {
                "cpuTimeNanoseconds": measured(time.process_time_ns(), "ns", "python-process-clock"),
                "peakMemoryBytes": unmeasured("bytes", "portable-process-api", "PLATFORM_COUNTER_UNSUPPORTED"),
                "diskReadBytes": unmeasured("bytes", "portable-process-api", "PLATFORM_COUNTER_UNSUPPORTED"),
                "diskWriteBytes": unmeasured("bytes", "portable-process-api", "PLATFORM_COUNTER_UNSUPPORTED"),
            }
        )
    return metrics


def queue_metrics(queue_depth: int | None, queue_capacity: int | None) -> dict[str, ResourceMetric]:
    if queue_depth is None or queue_capacity is None:
        return {
            "queueDepth": unmeasured("items", "mission-queue", "QUEUE_CONTEXT_NOT_AVAILABLE"),
            "queueCapacity": unmeasured("items", "mission-queue", "QUEUE_CONTEXT_NOT_AVAILABLE"),
        }
    if queue_depth < 0 or queue_capacity < 1 or queue_depth > queue_capacity:
        raise ValueError("Queue telemetry requires zero <= depth <= capacity and positive capacity.")
    return {
        "queueDepth": measured(queue_depth, "items", "mission-queue"),
        "queueCapacity": measured(queue_capacity, "items", "mission-queue"),
    }


def provider_metrics(usage: Mapping[str, object] | None, source: str) -> dict[str, ResourceMetric]:
    if usage is None:
        return {
            "providerCalls": unmeasured("calls", source, "VALIDATED_PROVIDER_USAGE_NOT_AVAILABLE"),
            "providerTokens": unmeasured("tokens", source, "VALIDATED_PROVIDER_USAGE_NOT_AVAILABLE"),
            "providerCostUsd": unmeasured("USD", source, "VALIDATED_PROVIDER_USAGE_NOT_AVAILABLE"),
        }
    calls = usage.get("providerCalls")
    tokens = usage.get("totalTokens")
    cost = usage.get("costUsd")
    if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
        raise ValueError("Validated provider calls must be a non-negative integer.")
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        raise ValueError("Validated provider tokens must be a non-negative integer.")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
        raise ValueError("Validated provider cost must be a non-negative number.")
    return {
        "providerCalls": measured(calls, "calls", source),
        "providerTokens": measured(tokens, "tokens", source),
        "providerCostUsd": measured(float(cost), "USD", source),
    }


def build_resource_charge(
    charge_id: str,
    scope: str,
    context: ChronosContext,
    local_metrics: Mapping[str, ResourceMetric],
    provider_usage: Mapping[str, object] | None,
    provider_source: str,
    queue_depth: int | None,
    queue_capacity: int | None,
    evidence_observed_at: datetime,
) -> dict[str, object]:
    metrics = dict(local_metrics)
    metrics.update(queue_metrics(queue_depth, queue_capacity))
    metrics.update(provider_metrics(provider_usage, provider_source))
    missing = [name for name in RESOURCE_METRIC_NAMES if name not in metrics]
    unknown = sorted(set(metrics).difference(RESOURCE_METRIC_NAMES))
    if missing or unknown:
        raise ValueError(f"Resource charge metric vector is invalid; missing={missing}; unknown={unknown}")
    unmeasured_names = [name for name in RESOURCE_METRIC_NAMES if metrics[name]["state"] != "MEASURED"]
    charge = {
        "schema": RESOURCE_CHARGE_SCHEMA,
        "chargeId": charge_id,
        "scope": scope,
        "completeness": "PARTIAL" if unmeasured_names else "COMPLETE",
        "unmeasured": unmeasured_names,
        "chronos": chronos_now(context, evidence_observed_at, 5.0),
        "metrics": {name: metrics[name] for name in RESOURCE_METRIC_NAMES},
        "authority": {
            "readOnlySampling": True,
            "optimization": False,
            "homeostaticAction": False,
            "providerAuthority": False,
        },
    }
    return charge | {"chargeSha256": canonical_sha256(charge)}
