"""Unit coverage for the group-aware episode deletion MCP adapter."""

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from graphiti_core import Graphiti

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import graphiti_mcp_server


class _FakeClient:
    """Stand-in whose remove_episode enforces the installed graphiti-core signature."""

    def __init__(self, driver):
        self.driver = driver
        self.clients = Mock(model_copy=Mock(return_value='scoped-clients'))
        self.calls = []  # shared by copy.copy, so scoped copies record here too
        self.error = None

    async def remove_episode(self, *args, **kwargs):
        inspect.signature(Graphiti.remove_episode).bind(self, *args, **kwargs)
        self.calls.append((self, args, kwargs))
        if self.error:
            raise self.error


def _client(default_driver, group_driver):
    default_driver.clone = Mock(return_value=group_driver)
    return _FakeClient(default_driver)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(graphiti_mcp_server, '_group_drivers', {})
    monkeypatch.setattr(
        graphiti_mcp_server,
        'config',
        SimpleNamespace(graphiti=SimpleNamespace(group_id='main')),
        raising=False,
    )


def _serve(monkeypatch, client):
    service = Mock(get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(graphiti_mcp_server, 'graphiti_service', service)


@pytest.mark.asyncio
async def test_delete_episode_calls_core_with_its_installed_signature(monkeypatch):
    # Regression: the server ran on PyPI graphiti-core, whose remove_episode takes
    # only episode_uuid; passing group_id raised TypeError on every call.
    default_driver, group_driver = Mock(), Mock()
    client = _client(default_driver, group_driver)
    _serve(monkeypatch, client)

    response = await graphiti_mcp_server.delete_episode('episode-1', group_id='coding-wiki')

    assert 'error' not in response, response
    assert response['message'] == 'Episode with UUID episode-1 deleted successfully'
    default_driver.clone.assert_called_once_with(database='coding-wiki')
    [(scoped_self, args, kwargs)] = client.calls
    assert args == ('episode-1',)
    assert kwargs == {}
    assert scoped_self.driver is group_driver
    assert scoped_self.clients == 'scoped-clients'
    # The shared client stays bound to the default graph.
    assert client.driver is default_driver


@pytest.mark.asyncio
async def test_delete_episode_without_group_uses_configured_default_group(monkeypatch):
    default_driver, group_driver = Mock(), Mock()
    client = _client(default_driver, group_driver)
    _serve(monkeypatch, client)

    await graphiti_mcp_server.delete_episode('episode-1')

    default_driver.clone.assert_called_once_with(database='main')


@pytest.mark.asyncio
async def test_delete_episode_reuses_client_when_clone_returns_same_driver(monkeypatch):
    default_driver = Mock()
    client = _client(default_driver, default_driver)
    _serve(monkeypatch, client)

    await graphiti_mcp_server.delete_episode('episode-1', group_id='main')

    [(scoped_self, _, _)] = client.calls
    assert scoped_self is client


@pytest.mark.asyncio
async def test_delete_episode_returns_error_response_when_core_delete_fails(monkeypatch):
    client = _client(Mock(), Mock())
    client.error = RuntimeError('not found')
    _serve(monkeypatch, client)

    response = await graphiti_mcp_server.delete_episode('episode-1', group_id='coding-wiki')

    assert 'not found' in response['error']
