import asyncio

import pytest

from pi_node_supervisor.runtime_lifecycle import RuntimeLifecycle


def test_reconfiguration_drains_work_and_blocks_new_operations():
    async def exercise():
        lifecycle = RuntimeLifecycle()
        first_started, second_started = asyncio.Event(), asyncio.Event()
        finish_work, finish_change = asyncio.Event(), asyncio.Event()
        change_started, later_started = asyncio.Event(), asyncio.Event()

        async def work(started):
            started.set()
            await finish_work.wait()

        async def change():
            change_started.set()
            await finish_change.wait()

        first = asyncio.create_task(lifecycle.run(lambda: work(first_started)))
        second = asyncio.create_task(lifecycle.run(lambda: work(second_started)))
        await asyncio.wait_for(
            asyncio.gather(first_started.wait(), second_started.wait()), 1
        )
        changing = asyncio.create_task(lifecycle.run(change, exclusive=True))
        await asyncio.sleep(0)
        later = asyncio.create_task(lifecycle.run(lambda: work(later_started)))
        await asyncio.sleep(0)
        assert not change_started.is_set()
        assert not later_started.is_set()
        finish_work.set()
        await asyncio.wait_for(change_started.wait(), 1)
        assert not later_started.is_set()
        finish_change.set()
        await asyncio.wait_for(asyncio.gather(first, second, changing, later), 1)
        assert later_started.is_set()

    asyncio.run(exercise())


def test_cancelled_request_does_not_release_running_device_work():
    async def exercise():
        lifecycle = RuntimeLifecycle()
        started, finish, changed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def work():
            started.set()
            await finish.wait()

        async def change():
            changed.set()

        request = asyncio.create_task(lifecycle.run(work))
        await started.wait()
        request.cancel()
        changing = asyncio.create_task(lifecycle.run(change, exclusive=True))
        await asyncio.sleep(0)
        assert not changed.is_set()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await request
        await asyncio.wait_for(changing, 1)
        assert changed.is_set()

    asyncio.run(exercise())


def test_cancelling_waiting_change_does_not_block_future_work():
    async def exercise():
        lifecycle = RuntimeLifecycle()
        started, finish = asyncio.Event(), asyncio.Event()

        async def work():
            started.set()
            await finish.wait()

        first = asyncio.create_task(lifecycle.run(work))
        await started.wait()
        changing = asyncio.create_task(lifecycle.run(work, exclusive=True))
        await asyncio.sleep(0)
        changing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await changing
        finish.set()
        await first
        await asyncio.wait_for(lifecycle.run(work), 1)

    asyncio.run(exercise())
