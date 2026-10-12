from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ubuntu.services.command_runner import CommandRunner, requires_shell
from ubuntu.services.job_manager import JobManager
from ubuntu.services.state_store import StateStore


class FakeSettings:
    def __init__(self, tmp_path: Path, *, shell: bool = False, timeout: int = 5,
                 max_output: int = 4096) -> None:
        self.default_working_directory = tmp_path
        self.command_timeout_seconds = timeout
        self.max_output_bytes = max_output
        self.enable_unrestricted_shell = shell
        self.max_read_bytes = 4096
        self.max_upload_bytes = 65536
        self.command_timeout = timeout


@pytest.fixture()
def runner(tmp_path: Path):
    settings = FakeSettings(tmp_path)
    state = StateStore(tmp_path / "state.json")
    jobs = JobManager(max_concurrent=2)
    return CommandRunner(settings, state), jobs, tmp_path


def _job(jobs: JobManager, command: str, cwd: Path, shell: bool = False):
    return jobs.new_job(
        admin_id=1, chat_id=1, command=command, shell_mode=shell, cwd=str(cwd)
    )


async def test_simple_command(runner):
    cmd_runner, jobs, cwd = runner
    job = _job(jobs, f"{sys.executable} -c \"print('hello')\"", cwd)
    await cmd_runner.execute(job)
    assert job.status == "done"
    assert job.exit_code == 0
    assert "hello" in job.stdout


async def test_non_zero_exit(runner):
    cmd_runner, jobs, cwd = runner
    job = _job(jobs, f"{sys.executable} -c \"import sys; sys.exit(3)\"", cwd)
    await cmd_runner.execute(job)
    assert job.status == "failed"
    assert job.exit_code == 3


async def test_stderr_captured(runner):
    cmd_runner, jobs, cwd = runner
    job = _job(jobs, f"{sys.executable} -c \"import sys; sys.stderr.write('bad')\"", cwd)
    await cmd_runner.execute(job)
    assert "bad" in job.stderr


async def test_timeout_kills_process(tmp_path):
    settings = FakeSettings(tmp_path, timeout=1)
    state = StateStore(tmp_path / "state.json")
    jobs = JobManager(max_concurrent=1)
    cmd_runner = CommandRunner(settings, state)
    job = _job(jobs, f"{sys.executable} -c \"import time; time.sleep(30)\"", tmp_path)
    await cmd_runner.execute(job)
    assert job.status == "timeout"


async def test_shell_mode_disabled_by_default(runner):
    cmd_runner, jobs, cwd = runner
    job = _job(jobs, "echo hi | cat", cwd, shell=True)
    await cmd_runner.execute(job)
    assert job.status == "failed"
    assert "disabled" in job.note.lower()


async def test_shell_mode_when_enabled(tmp_path):
    settings = FakeSettings(tmp_path, shell=True)
    state = StateStore(tmp_path / "state.json")
    jobs = JobManager(max_concurrent=1)
    cmd_runner = CommandRunner(settings, state)
    job = _job(jobs, "echo hello | tr a-z A-Z", tmp_path, shell=True)
    await cmd_runner.execute(job)
    assert job.status == "done"
    assert "HELLO" in job.stdout


async def test_output_truncation(tmp_path):
    settings = FakeSettings(tmp_path, max_output=64)
    state = StateStore(tmp_path / "state.json")
    jobs = JobManager(max_concurrent=1)
    cmd_runner = CommandRunner(settings, state)
    job = _job(jobs, f"{sys.executable} -c \"print('x'*5000)\"", tmp_path)
    await cmd_runner.execute(job)
    assert job.truncated is True
    assert len(job.stdout) <= 64


async def test_cwd_persistence(runner):
    cmd_runner, _jobs, tmp_path = runner
    sub = tmp_path / "sub"
    sub.mkdir()
    cmd_runner.set_cwd(1, str(sub))
    assert cmd_runner.get_cwd(1) == sub.resolve()

    # A different admin keeps the default.
    assert cmd_runner.get_cwd(2) == tmp_path


async def test_cd_rejects_missing_directory(runner):
    cmd_runner, _jobs, _tmp = runner
    with pytest.raises(FileNotFoundError):
        cmd_runner.set_cwd(1, "/definitely/not/here")


async def test_cancellation(tmp_path):
    settings = FakeSettings(tmp_path, timeout=60)
    state = StateStore(tmp_path / "state.json")
    jobs = JobManager(max_concurrent=1)
    cmd_runner = CommandRunner(settings, state)
    job = _job(jobs, f"{sys.executable} -c \"import time; time.sleep(30)\"", tmp_path)

    async def worker(record):
        await cmd_runner.execute(record)

    task = jobs.spawn(job, worker)
    await asyncio.sleep(0.7)
    assert job.status == "running"
    await jobs.cancel(job)
    assert job.status == "cancelled"
    assert task.done() or task.cancelled()


async def test_command_not_found(runner):
    cmd_runner, jobs, cwd = runner
    job = _job(jobs, "definitely-not-a-real-binary-12345", cwd)
    await cmd_runner.execute(job)
    assert job.status == "failed"
    assert "not found" in job.note.lower()


def test_requires_shell_detection():
    assert requires_shell("ls -la | head")
    assert requires_shell("echo hi > file")
    assert not requires_shell("ls -la")
