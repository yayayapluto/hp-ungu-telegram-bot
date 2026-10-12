from __future__ import annotations

import asyncio

import pytest

from ubuntu.services.job_manager import JobManager


async def test_job_ids_are_unique():
    manager = JobManager(max_concurrent=2)
    a = manager.new_job(admin_id=1, chat_id=1, command="true", shell_mode=False, cwd="/")
    b = manager.new_job(admin_id=1, chat_id=1, command="true", shell_mode=False, cwd="/")
    assert a.job_id != b.job_id


async def test_concurrency_limit_is_enforced():
    manager = JobManager(max_concurrent=1)
    order: list[str] = []

    async def worker(job):
        order.append(f"start-{job.job_id}")
        await asyncio.sleep(0.15)
        order.append(f"end-{job.job_id}")

    jobs = [
        manager.new_job(admin_id=1, chat_id=1, command="x", shell_mode=False, cwd="/")
        for _ in range(3)
    ]
    tasks = [manager.spawn(job, worker) for job in jobs]
    await asyncio.gather(*tasks)

    starts = [entry for entry in order if entry.startswith("start-")]
    ends = [entry for entry in order if entry.startswith("end-")]
    assert len(starts) == 3
    assert order.index(ends[0]) < order.index(starts[1])


async def test_find_by_prefix():
    manager = JobManager(max_concurrent=1)
    job = manager.new_job(admin_id=1, chat_id=1, command="true", shell_mode=False, cwd="/")
    assert manager.find(job.job_id[:6]) is job
    assert manager.find("nope") is None


async def test_cancel_all_for_admin():
    manager = JobManager(max_concurrent=2)

    async def worker(job):
        await asyncio.sleep(5)

    jobs = [
        manager.new_job(admin_id=1, chat_id=1, command="x", shell_mode=False, cwd="/")
        for _ in range(2)
    ]
    for job in jobs:
        manager.spawn(job, worker)

    await asyncio.sleep(0.1)
    cancelled = await manager.cancel_all(1)
    assert cancelled == 2
    assert all(job.status == "cancelled" for job in jobs)


async def test_listing_filters_by_admin():
    manager = JobManager(max_concurrent=1)
    manager.new_job(admin_id=1, chat_id=1, command="a", shell_mode=False, cwd="/")
    manager.new_job(admin_id=2, chat_id=2, command="b", shell_mode=False, cwd="/")
    assert len(manager.list(1)) == 1
    assert len(manager.list()) == 2


async def test_worker_exception_is_recorded():
    manager = JobManager(max_concurrent=1)
    job = manager.new_job(admin_id=1, chat_id=1, command="x", shell_mode=False, cwd="/")

    async def worker(_job):
        raise RuntimeError("boom")

    await manager.spawn(job, worker)
    assert job.status == "failed"
    assert "boom" in job.note
