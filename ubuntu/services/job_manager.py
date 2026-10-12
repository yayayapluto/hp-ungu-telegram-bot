"""Asynchronous job tracking, concurrency limiting and cancellation."""
from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ubuntu.utils.logging_config import get_logger

log = get_logger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_TIMEOUT, STATUS_CANCELLED})


@dataclass
class JobRecord:
    job_id: str
    admin_id: int
    chat_id: int
    command: str
    shell_mode: bool
    cwd: str
    status: str = STATUS_QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    note: str = ""
    cancelled: bool = False
    task: Optional[asyncio.Task] = None
    process: Any = None

    @property
    def duration(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    @property
    def is_active(self) -> bool:
        return self.status in {STATUS_QUEUED, STATUS_RUNNING}

    def short(self) -> str:
        return self.job_id[:8]


class JobManager:
    """Owns job records and their asyncio tasks."""

    def __init__(self, max_concurrent: int = 3, history_limit: int = 60) -> None:
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._jobs: "OrderedDict[str, JobRecord]" = OrderedDict()
        self._history_limit = max(10, history_limit)
        self._counter = itertools.count(1)

    # ------------------------------------------------------------- creation
    def new_job(self, *, admin_id: int, chat_id: int, command: str,
                shell_mode: bool, cwd: str) -> JobRecord:
        job_id = f"{next(self._counter):04d}-{uuid.uuid4().hex[:8]}"
        job = JobRecord(
            job_id=job_id,
            admin_id=admin_id,
            chat_id=chat_id,
            command=command,
            shell_mode=shell_mode,
            cwd=cwd,
        )
        self._jobs[job_id] = job
        self._prune()
        return job

    # -------------------------------------------------------------- queries
    def get(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def find(self, prefix: str) -> Optional[JobRecord]:
        prefix = prefix.strip()
        if not prefix:
            return None
        if prefix in self._jobs:
            return self._jobs[prefix]
        matches = [j for jid, j in self._jobs.items() if jid.startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    def list(self, admin_id: Optional[int] = None, include_finished: bool = True) -> List[JobRecord]:
        jobs = list(self._jobs.values())
        if admin_id is not None:
            jobs = [j for j in jobs if j.admin_id == admin_id]
        if not include_finished:
            jobs = [j for j in jobs if j.is_active]
        return jobs

    def active(self, admin_id: Optional[int] = None) -> List[JobRecord]:
        return self.list(admin_id, include_finished=False)

    # ------------------------------------------------------------ execution
    def spawn(self, job: JobRecord, worker: Callable[[JobRecord], Awaitable[None]]) -> asyncio.Task:
        """Schedule *worker* for *job*, respecting the concurrency limit."""

        async def _runner() -> None:
            async with self._semaphore:
                if job.cancelled:
                    job.status = STATUS_CANCELLED
                    job.finished_at = time.time()
                    return
                job.status = STATUS_RUNNING
                job.started_at = time.time()
                try:
                    await worker(job)
                except asyncio.CancelledError:
                    job.status = STATUS_CANCELLED
                    job.note = job.note or "Cancelled"
                    raise
                except Exception as exc:  # noqa: BLE001 - surfaced to the admin
                    log.exception("Job %s failed", job.job_id)
                    job.status = STATUS_FAILED
                    job.note = f"{type(exc).__name__}: {exc}"
                finally:
                    if job.finished_at is None:
                        job.finished_at = time.time()

        task = asyncio.create_task(_runner(), name=f"job-{job.job_id}")
        job.task = task
        return task

    async def cancel(self, job: JobRecord, reason: str = "Cancelled by administrator") -> bool:
        """Cancel *job*; returns True when the job was still active."""
        if job.status in TERMINAL_STATUSES:
            return False
        job.cancelled = True
        job.note = reason

        process = job.process
        if process is not None and getattr(process, "returncode", None) is None:
            try:
                process.kill()
            except (ProcessLookupError, PermissionError) as exc:  # pragma: no cover
                log.warning("Could not kill process for job %s: %s", job.job_id, exc)

        task = job.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if job.status not in TERMINAL_STATUSES:
            job.status = STATUS_CANCELLED
            job.finished_at = time.time()
        return True

    async def cancel_all(self, admin_id: Optional[int] = None) -> int:
        cancelled = 0
        for job in self.active(admin_id):
            if await self.cancel(job):
                cancelled += 1
        return cancelled

    # ----------------------------------------------------------- maintenance
    def _prune(self) -> None:
        if len(self._jobs) <= self._history_limit:
            return
        for job_id, job in list(self._jobs.items()):
            if len(self._jobs) <= self._history_limit:
                break
            if not job.is_active:
                self._jobs.pop(job_id, None)

    async def shutdown(self) -> None:
        await self.cancel_all()
