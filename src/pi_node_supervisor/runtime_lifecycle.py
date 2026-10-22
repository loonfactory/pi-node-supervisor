"""Coordinate concurrent operations with exclusive runtime reconfiguration."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

T = TypeVar("T")


class RuntimeLifecycle:
    def __init__(self):
        self._condition = asyncio.Condition()
        self._active = 0
        self._changing = False
        self._waiting_changes = 0

    @asynccontextmanager
    async def _slot(self, exclusive: bool):
        async with self._condition:
            if exclusive:
                self._waiting_changes += 1
                try:
                    await self._condition.wait_for(
                        lambda: not self._changing and self._active == 0
                    )
                    self._changing = True
                finally:
                    self._waiting_changes -= 1
                    self._condition.notify_all()
            else:
                await self._condition.wait_for(
                    lambda: not self._changing and not self._waiting_changes
                )
                self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                if exclusive:
                    self._changing = False
                else:
                    self._active -= 1
                self._condition.notify_all()

    async def run(self, operation: Callable[[], Awaitable[T]], *, exclusive=False) -> T:
        async with self._slot(exclusive):
            task = asyncio.create_task(operation())
            # A cancelled HTTP request must not release the slot while a device
            # operation in to_thread is still running. Drain it before propagating.
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                if not task.cancelled():
                    task.exception()  # retrieve any failure before propagating cancellation
                raise asyncio.CancelledError
            return task.result()
