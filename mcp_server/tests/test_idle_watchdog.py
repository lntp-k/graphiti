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


def test_seconds_idle_is_zero_while_a_call_is_in_flight():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)

    with watchdog.track_activity():
        clock.advance(120)  # far past the timeout
        assert watchdog.seconds_idle() == 0


def test_seconds_idle_resumes_counting_after_the_call_finishes():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)

    with watchdog.track_activity():
        clock.advance(120)
    clock.advance(7)

    assert watchdog.seconds_idle() == 7


def test_track_activity_nests_for_concurrent_calls():
    """Two overlapping calls: idle must not resume until the LAST one exits."""
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)

    with watchdog.track_activity():
        with watchdog.track_activity():
            pass
        clock.advance(120)
        assert watchdog.seconds_idle() == 0

    assert watchdog.seconds_idle() == 0


async def _pump_clock_while(clock: FakeClock, predicate, step: float = 0.02) -> None:
    """Advance `clock` and yield in a tight loop until `predicate()` is true.

    `IdleTimeoutWatchdog.run()` sleeps on *real* `asyncio.sleep(check_interval)`
    regardless of the injected fake clock, so a test needs real wall-clock time
    to actually pass for its internal loop to wake up and re-check
    `seconds_idle()`. Driving this via `asyncio.wait_for(..., timeout=T)`
    lets the loop spin (consuming real time) for up to `T` real seconds while
    also advancing the fake clock every iteration.
    """
    while not predicate():
        clock.advance(step)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_run_does_not_fire_while_a_call_is_in_flight_past_the_timeout():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(0.05, clock=clock, check_interval_seconds=0.01)

    task = asyncio.create_task(watchdog.run())
    with watchdog.track_activity():
        # Race the fake clock far past the timeout for a real 0.1s: if the fix
        # is broken, the watchdog's real internal ticks (every ~0.01s) would
        # see seconds_idle() > timeout and finish `task` well within that
        # window, so this deliberately consumes real wall-clock time rather
        # than a single `sleep(0)` yield that proves nothing.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_pump_clock_while(clock, task.done, step=2.0), timeout=0.1)
        assert not task.done()

    # Idle again from here: the timeout should now fire.
    await asyncio.wait_for(_pump_clock_while(clock, task.done), timeout=2)
    assert task.done()


@pytest.mark.asyncio
async def test_wrap_with_activity_tracking_keeps_watchdog_from_firing_during_a_slow_call():
    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(0.05, clock=clock, check_interval_seconds=0.01)
    call_started = asyncio.Event()
    finish_call = asyncio.Event()

    async def slow_call_tool(*args, **kwargs):
        call_started.set()
        await finish_call.wait()
        return 'done'

    wrapped = wrap_with_activity_tracking(slow_call_tool, watchdog)
    watchdog_task = asyncio.create_task(watchdog.run())
    call_task = asyncio.create_task(wrapped())

    await call_started.wait()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            _pump_clock_while(clock, watchdog_task.done, step=2.0), timeout=0.1
        )
    assert not watchdog_task.done()

    finish_call.set()
    result = await call_task
    assert result == 'done'

    watchdog_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watchdog_task


@pytest.mark.asyncio
async def test_wrapping_tool_manager_call_tool_intercepts_the_real_fastmcp_dispatch_path():
    """Prove the wiring point is real, not just a hand-written stand-in.

    graphiti_mcp_server.py patches `mcp._tool_manager.call_tool` -- a private
    FastMCP attribute. If a library upgrade renames or bypasses it, this
    test (not just the wrap_with_activity_tracking unit tests above, which
    only ever exercise a fake function) is what would catch it.
    """
    from mcp.server.fastmcp import FastMCP

    clock = FakeClock()
    watchdog = IdleTimeoutWatchdog(60, clock=clock)
    test_mcp: FastMCP = FastMCP('test-server')

    @test_mcp.tool()
    def ping(message: str) -> str:
        return f'pong: {message}'

    test_mcp._tool_manager.call_tool = wrap_with_activity_tracking(
        test_mcp._tool_manager.call_tool, watchdog
    )
    clock.advance(45)

    # This is the same public entry point FastMCP's low-level server calls
    # for every `tools/call` request over any transport.
    result = await test_mcp.call_tool('ping', {'message': 'hi'})

    assert watchdog.seconds_idle() == 0
    assert 'pong: hi' in str(result)


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
