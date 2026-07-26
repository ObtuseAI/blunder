"""Project-local stack detection and process ownership for Blunder vNext."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

from blunder.api.contracts import ProjectPolicyV1


STACK_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("package-lock.json", "node-npm"),
    ("pnpm-lock.yaml", "node-pnpm"),
    ("yarn.lock", "node-yarn"),
    ("Cargo.lock", "rust"),
    ("go.mod", "go"),
)


@dataclass(frozen=True)
class StackDetectionV1:
    schema: str
    project_root: str
    stacks: tuple[str, ...]
    lockfiles: tuple[str, ...]


@dataclass(frozen=True)
class OwnedProcessV1:
    process_id: str
    pid: int
    task_id: str
    command: tuple[str, ...]
    working_directory: str
    log_path: str
    cleanup_policy: str


@dataclass
class _RunningProcess:
    record: OwnedProcessV1
    process: subprocess.Popen[bytes]
    log_handle: BinaryIO


def detect_project_stacks(project_root: Path) -> StackDetectionV1:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project root does not exist: {root}")
    markers = tuple(name for name, _stack in STACK_MARKERS if (root / name).is_file())
    stacks = tuple(dict.fromkeys(stack for name, stack in STACK_MARKERS if name in markers))
    return StackDetectionV1("obtuse.blunder.stack-detection.v1", str(root), stacks, markers)


def reserve_loopback_port() -> tuple[socket.socket, int]:
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    return reservation, int(reservation.getsockname()[1])


def command_is_allowed(command: Sequence[str], policy: ProjectPolicyV1) -> bool:
    normalized = tuple(str(part) for part in command)
    return normalized in policy.allowed_commands


def process_identity(task_id: str, command: Sequence[str], working_directory: Path) -> str:
    payload = "\0".join((task_id, str(working_directory.resolve()), *command)).encode("utf-8")
    return f"process-{hashlib.sha256(payload).hexdigest()[:24]}"


class OwnedProcessRegistry:
    """Owns only processes it starts and rejects cleanup for unknown identities."""

    def __init__(self, policy: ProjectPolicyV1, artifacts_root: Path) -> None:
        self._policy = policy
        self._artifacts_root = artifacts_root.resolve()
        self._processes: dict[str, _RunningProcess] = {}
        self._lock = threading.RLock()

    def start(self, task_id: str, command: Sequence[str], working_directory: Path, cleanup_policy: str) -> OwnedProcessV1:
        normalized = tuple(str(part) for part in command)
        directory = working_directory.resolve()
        if str(directory) not in self._policy.allowed_roots:
            raise PermissionError(f"Process working directory is outside allowed roots: {directory}")
        if not command_is_allowed(normalized, self._policy):
            raise PermissionError(f"Process command is not declared in project policy: {normalized}")
        if cleanup_policy not in {"terminate-on-cancel", "terminate-on-host-exit"}:
            raise ValueError(f"Unsupported cleanup policy: {cleanup_policy}")
        identity = process_identity(task_id, normalized, directory)
        with self._lock:
            if identity in self._processes:
                raise ValueError(f"Owned process identity is already active: {identity}")
            log_path = self._artifacts_root / "processes" / identity / "process.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("xb")
            try:
                process = subprocess.Popen(
                    normalized,
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env={key: value for key, value in os.environ.items() if key.upper() not in {"OPENAI_API_KEY", "OPENROUTER_API_KEY"}},
                )
            except BaseException:
                log_handle.close()
                raise
            record = OwnedProcessV1(identity, process.pid, task_id, normalized, str(directory), str(log_path), cleanup_policy)
            self._processes[identity] = _RunningProcess(record, process, log_handle)
            return record

    def stop(self, process_id: str) -> OwnedProcessV1:
        with self._lock:
            running = self._processes.get(process_id)
            if running is None:
                raise PermissionError(f"Blunder will not terminate an unowned process: {process_id}")
            if running.process.poll() is None:
                running.process.terminate()
                try:
                    running.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    running.process.kill()
                    running.process.wait(timeout=5)
            running.log_handle.close()
            del self._processes[process_id]
            return running.record

    def active(self) -> tuple[OwnedProcessV1, ...]:
        with self._lock:
            return tuple(item.record for item in self._processes.values() if item.process.poll() is None)
