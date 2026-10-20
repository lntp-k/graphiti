"""Drain SDK notification subscriptions before Uvicorn waits for connections."""

from collections.abc import Callable

import uvicorn
from mcp.server.subscriptions import ListenHandler
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR


class DrainableSubscriptions:
    """Close current listeners and reject listeners admitted during shutdown."""

    def __init__(self, handler: ListenHandler):
        self.handler = handler
        self.draining = False

    async def __call__(self, ctx, params):
        if self.draining:
            raise MCPError(INTERNAL_ERROR, 'Server shutting down')
        return await self.handler(ctx, params)

    def close(self) -> None:
        if not self.draining:
            self.draining = True
            self.handler.close()


def install_subscription_drain(mcp) -> Callable[[], None] | None:
    """Use SDK public registration after streamable_http_app creates the manager."""
    app = mcp.session_manager.app
    entry = app.get_request_handler('subscriptions/listen')
    if entry is None:
        return None
    if isinstance(entry.handler, DrainableSubscriptions):
        return entry.handler.close
    if not isinstance(entry.handler, ListenHandler):
        raise RuntimeError(
            'Unsupported SDK subscriptions/listen handler; shutdown drain unavailable'
        )
    handler = DrainableSubscriptions(entry.handler)
    app.add_request_handler('subscriptions/listen', entry.params_type, handler)
    return handler.close


class SubscriptionDrainingServer(uvicorn.Server):
    def __init__(self, config, *, drain_subscriptions: Callable[[], None] | None = None):
        super().__init__(config)
        self.drain_subscriptions = drain_subscriptions

    async def shutdown(self, sockets=None):
        # No await between closing listeners and Uvicorn closing its listening
        # sockets. Already-dispatched late listeners are fenced by the wrapper.
        if self.drain_subscriptions is not None:
            self.drain_subscriptions()
        await super().shutdown(sockets=sockets)
