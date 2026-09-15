"""The episode queue worker must keep the idle watchdog from firing mid-processing.

`add_episode`/`add_episode_task` return as soon as the episode is queued; the
actual LLM extraction runs later in `_process_episode_queue`'s background
task. If nothing marks that background work as "activity", the idle watchdog
can fire and kill the process while a queued episode is still being written
to the graph (see the fable/opus review of commit cbe8fd1).
"""

import asyncio

import pytest
from unittest.mock import AsyncMock

from services.queue_service import QueueService
from utils.idle_watchdog import IdleTimeoutWatchdog


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


async def _pump_clock_while(clock: FakeClock, predicate, step: float = 2.0) -> None:
    while not predicate():
        clock.advance(step)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_queue_worker_keeps_watchdog_from_firing_while_processing():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(0.05, clock=clock, check_interval_seconds=0.01)
    service = QueueService()
    service.set_idle_watchdog(watchdog)
    await service.initialize(AsyncMock())

    started = asyncio.Event()
    finish = asyncio.Event()
    finished = asyncio.Event()

    async def slow_process_episode() -> None:
        started.set()
        await finish.wait()
        finished.set()

    await service.add_episode_task('legal-eval', slow_process_episode)
    await started.wait()

    watchdog_task = asyncio.create_task(watchdog.run())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            _pump_clock_while(clock, watchdog_task.done), timeout=0.1
        )
    assert not watchdog_task.done()

    finish.set()
    await asyncio.wait_for(finished.wait(), timeout=1)

    watchdog_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watchdog_task


@pytest.mark.asyncio
async def test_queue_service_with_no_watchdog_set_still_processes_episodes():
    """Sanity check: the watchdog wiring is optional, not required."""
    service = QueueService()
    await service.initialize(AsyncMock())
    done = asyncio.Event()

    async def process() -> None:
        done.set()

    await service.add_episode_task('legal-eval', process)
    await asyncio.wait_for(done.wait(), timeout=1)
