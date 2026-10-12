"""Executes commands inside the Ubuntu userspace."""
from __future__ import annotations

import asyncio
import os
import shlex
import signal
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ubuntu.config import Settings
from ubuntu.services.job_manager import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    JobRecord,
)
from ubuntu.services.state_store import StateStore
from ubuntu.utils.logging_config import get_logger

log = get_logger(__name__)

_CHUNK = 8192
SHELL_METACHARACTERS = set("|&;<>()$`\\\"'\n*?[]{}~!")


class CommandRunner:
    """Runs commands inside Ubuntu with timeouts, limits and cancellation."""

    def __init__(self, settings: Settings, state: StateStore) -> None:
        self.settings = settings
        self.state = state

    # ------------------------------------------------------------------- cwd
    def get_cwd(self, admin_id: int) -> Path:
        stored = self.state.get(admin_id, "cwd")
        if stored:
            candidate = Path(stored)
            if candidate.is_dir():
                return candidate
        return self.settings.default_working_directory

    def set_cwd(self, admin_id: int, raw: str) -> Path:
        current = self.get_cwd(admin_id)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = current / candidate
        resolved = candidate.resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"No such directory: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {resolved}")
        self.state.set(admin_id, "cwd", str(resolved))
        return resolved

    def reset_cwd(self, admin_id: int) -> Path:
        self.state.delete(admin_id, "cwd")
        return self.settings.default_working_directory

    # -------------------------------------------------------------- execution
    def _build_env(self) -> dict:
        env = os.environ.copy()
        env["TERM"] = "dumb"
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("LANG", "C.UTF-8")
        return env

    async def execute(
        self,
        job: JobRecord,
        on_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> JobRecord:
        settings = self.settings
        limit = settings.max_output_bytes
        timeout = settings.command_timeout_seconds

        if job.shell_mode and not settings.enable_unrestricted_shell:
            job.status = STATUS_FAILED
            job.note = "Shell mode is disabled (ENABLE_UNRESTRICTED_SHELL=false)."
            return job

        cwd = job.cwd if Path(job.cwd).is_dir() else str(settings.default_working_directory)
        job.cwd = cwd
        env = self._build_env()

        stdout_buf: bytearray = bytearray()
        stderr_buf: bytearray = bytearray()
        counters = {"stdout_total": 0, "stderr_total": 0}

        try:
            if job.shell_mode:
                proc = await asyncio.create_subprocess_shell(
                    job.command,
                    cwd=cwd,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                argv = shlex.split(job.command)
                if not argv:
                    job.status = STATUS_FAILED
                    job.note = "Empty command."
                    return job
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=cwd,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
        except FileNotFoundError as exc:
            job.status = STATUS_FAILED
            job.note = f"Command not found: {exc}"
            return job
        except (PermissionError, OSError) as exc:
            job.status = STATUS_FAILED
            job.note = f"Could not start command: {exc}"
            return job

        job.process = proc

        pump_out = asyncio.create_task(
            self._pump(proc.stdout, stdout_buf, limit, counters, "stdout_total", on_chunk)
        )
        pump_err = asyncio.create_task(
            self._pump(proc.stderr, stderr_buf, limit, counters, "stderr_total", None)
        )

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate(proc)
        except asyncio.CancelledError:
            await self._terminate(proc)
            await asyncio.gather(pump_out, pump_err, return_exceptions=True)
            job.status = STATUS_CANCELLED
            job.note = job.note or "Cancelled"
            job.stdout = stdout_buf.decode("utf-8", "replace")
            job.stderr = stderr_buf.decode("utf-8", "replace")
            raise

        await asyncio.gather(pump_out, pump_err, return_exceptions=True)

        job.stdout = stdout_buf.decode("utf-8", "replace")
        job.stderr = stderr_buf.decode("utf-8", "replace")
        job.truncated = (
            counters["stdout_total"] > limit or counters["stderr_total"] > limit
        )
        job.exit_code = proc.returncode

        if timed_out:
            job.status = STATUS_TIMEOUT
            job.note = f"Timed out after {timeout}s (process killed)."
        elif job.cancelled:
            job.status = STATUS_CANCELLED
        elif proc.returncode == 0:
            job.status = STATUS_DONE
        else:
            job.status = STATUS_FAILED
        return job

    async def _pump(
        self,
        stream: Optional[asyncio.StreamReader],
        buffer: bytearray,
        limit: int,
        counters: dict,
        counter_key: str,
        on_chunk: Optional[Callable[[str], Awaitable[None]]],
    ) -> None:
        if stream is None:
            return
        while True:
            try:
                chunk = await stream.read(_CHUNK)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                return
            if not chunk:
                return
            counters[counter_key] += len(chunk)
            room = limit - len(buffer)
            if room > 0:
                buffer.extend(chunk[:room])
            if on_chunk is not None:
                try:
                    await on_chunk(chunk.decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001 - progress must never break execution
                    pass

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:  # pragma: no cover
            log.warning("Process %s did not die after SIGKILL", proc.pid)


def requires_shell(command: str) -> bool:
    """Heuristic: does *command* contain shell metacharacters?"""
    return any(ch in SHELL_METACHARACTERS for ch in command)
