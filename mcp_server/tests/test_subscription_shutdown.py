"""Subscription admission and shutdown ordering without network or graph data."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.exceptions import MCPError

from services.subscription_shutdown import (
    DrainableSubscriptions,
    SubscriptionDrainingServer,
    install_subscription_drain,
)


def test_install_uses_public_manager_and_preserves_parameter_model():
    mcp = MCPServer('isolated-drain-registration')
    mcp.streamable_http_app(host='127.0.0.1')
    app = mcp.session_manager.app
    before = app.get_request_handler('subscriptions/listen')
    drain = install_subscription_drain(mcp)
    after = app.get_request_handler('subscriptions/listen')
    assert after.params_type is before.params_type
    assert isinstance(after.handler, DrainableSubscriptions)
    assert after.handler.handler is before.handler
    assert install_subscription_drain(mcp) == drain


@pytest.mark.asyncio
async def test_existing_handler_preserved_and_late_subscription_rejected():
    class Listener(ListenHandler):
        async def __call__(self, ctx, params):
            return (ctx, params)

    listener = Listener(InMemorySubscriptionBus())
    listener.close = Mock(wraps=listener.close)
    wrapped = DrainableSubscriptions(listener)
    assert await wrapped('context', 'params') == ('context', 'params')
    wrapped.close()
    wrapped.close()
    listener.close.assert_called_once_with()
    with pytest.raises(MCPError, match='Server shutting down'):
        await wrapped('late context', 'late params')


def test_unknown_sdk_handler_fails_before_serving():
    app = SimpleNamespace(
        get_request_handler=lambda _: SimpleNamespace(handler=AsyncMock(), params_type=dict)
    )
    with pytest.raises(RuntimeError, match='subscriptions/listen'):
        install_subscription_drain(SimpleNamespace(session_manager=SimpleNamespace(app=app)))
    app.get_request_handler = lambda _: None
    assert (
        install_subscription_drain(SimpleNamespace(session_manager=SimpleNamespace(app=app)))
        is None
    )


@pytest.mark.asyncio
async def test_drain_precedes_uvicorn_connection_wait(monkeypatch):
    order = []

    async def shutdown(self, sockets=None):
        order.append(('uvicorn', sockets))

    monkeypatch.setattr(uvicorn.Server, 'shutdown', shutdown)
    server = SubscriptionDrainingServer(
        uvicorn.Config(AsyncMock()), drain_subscriptions=lambda: order.append(('drain', None))
    )
    await server.shutdown(sockets=['socket-sentinel'])
    assert order == [('drain', None), ('uvicorn', ['socket-sentinel'])]
