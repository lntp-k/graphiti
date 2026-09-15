"""Idle-timeout watchdog for the stdio-transport MCP process.

A stdio Graphiti MCP server is spawned per client session (Claude Code,
Hermes Agent) and has no way to know its client is done with it short of the
client closing stdin or its whole process tree dying. When neither happens
cleanly, the server just sits there holding ~500MB+ RSS indefinitely, and
these accumulate across sessions until the host runs out of memory (see the
2026-09-15 mnsdb-weekly OOM incident).

This watchdog tracks the last time an MCP tool was actually called and
exits the process after `timeout_seconds` of silence, so a leaked stdio
session frees its memory instead of piling up. If the client is still
around and calls a tool again later, its MCP client is expected to respawn
the process on demand -- the same way it did the first time.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

_R = TypeVar('_R')


class IdleTimeoutWatchdog:
    """Tracks MCP tool activity and signals shutdown after a period of silence."""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        check_interval_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError('timeout_seconds must be positive')
        self._timeout_seconds = timeout_seconds
        self._check_interval_seconds = check_interval_seconds
        self._clock = clock
        self._last_activity = clock()

    def touch(self) -> None:
        """Record MCP activity, resetting the idle clock."""
        self._last_activity = self._clock()

    def seconds_idle(self) -> float:
        return self._clock() - self._last_activity

    async def run(self) -> None:
        """Return once `timeout_seconds` pass without a `touch()` call.

        Intended to be raced against the transport's serve loop (e.g. via
        `asyncio.wait(..., return_when=asyncio.FIRST_COMPLETED)`); the caller
        treats this returning first as "shut down now".
        """
        while True:
            remaining = self._timeout_seconds - self.seconds_idle()
            if remaining <= 0:
                logger.warning(
                    'No MCP activity for %.0fs (limit %.0fs) -- shutting down idle server',
                    self.seconds_idle(),
                    self._timeout_seconds,
                )
                return
            await asyncio.sleep(min(remaining, self._check_interval_seconds))


def wrap_with_activity_tracking(
    func: Callable[..., Awaitable[_R]], watchdog: IdleTimeoutWatchdog
) -> Callable[..., Awaitable[_R]]:
    """Wrap an async callable so every invocation touches `watchdog` first.

    Touches before calling through (not after) so a call that hangs or
    raises still counts as activity -- the watchdog measures "is something
    talking to this server", not "did the last call succeed".
    """

    @functools.wraps(func)
    async def _wrapped(*args, **kwargs) -> _R:
        watchdog.touch()
        return await func(*args, **kwargs)

    return _wrapped
