"""Tests for the stdio idle-timeout watchdog (utils/idle_watchdog.py).

Covers the accumulation problem where a stdio-transport Graphiti MCP process
outlives the client session that spawned it (see decision doc referenced in
utils/idle_watchdog.py): the watchdog must return from `run()` once
`timeout_seconds` pass without a `touch()` call, and must keep waiting as
long as `touch()` keeps resetting the clock.
"""

import asyncio

import pytest

from utils.idle_watchdog import IdleTimeoutWatchdog, wrap_with_activity_tracking


class FakeClock:
    """Manually-advanced clock so tests don't depend on real wall time."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_rejects_non_positive_timeout():
    with pytest.raises(ValueError):
        IdleTimeoutWatchdog(0, clock=FakeClock())


def test_seconds_idle_reflects_elapsed_time_since_touch():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)

    clock.advance(10)

    assert watchdog.seconds_idle() == 10


def test_touch_resets_the_idle_clock():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)

    clock.advance(59)
    watchdog.touch()
    clock.advance(5)

    assert watchdog.seconds_idle() == 5


@pytest.mark.asyncio
async def test_run_returns_once_timeout_elapses_with_no_activity():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(0.05, clock=clock, check_interval_seconds=0.01)

    async def advance_clock_until_done(task: asyncio.Task) -> None:
        while not task.done():
            clock.advance(0.01)
            await asyncio.sleep(0)

    task = asyncio.create_task(watchdog.run())
    await asyncio.wait_for(advance_clock_until_done(task), timeout=2)

    assert task.done()


@pytest.mark.asyncio
async def test_wrap_with_activity_tracking_touches_watchdog_before_calling_through():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)
    calls = []

    async def real_call_tool(name, arguments):
        calls.append((name, arguments))
        return 'result'

    wrapped = wrap_with_activity_tracking(real_call_tool, watchdog)
    clock.advance(30)

    result = await wrapped('search_nodes', {'query': 'x'})

    assert result == 'result'
    assert calls == [('search_nodes', {'query': 'x'})]
    assert watchdog.seconds_idle() == 0


@pytest.mark.asyncio
async def test_wrap_with_activity_tracking_still_touches_when_call_raises():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)

    async def failing_call_tool(*args, **kwargs):
        raise RuntimeError('boom')

    wrapped = wrap_with_activity_tracking(failing_call_tool, watchdog)
    clock.advance(10)

    with pytest.raises(RuntimeError):
        await wrapped()

    assert watchdog.seconds_idle() == 0


@pytest.mark.asyncio
async def test_run_keeps_waiting_while_touch_keeps_resetting_the_clock():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(0.05, clock=clock, check_interval_seconds=0.01)

    task = asyncio.create_task(watchdog.run())
    try:
        for _ in range(5):
            clock.advance(0.03)
            watchdog.touch()
            await asyncio.sleep(0)
            assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
