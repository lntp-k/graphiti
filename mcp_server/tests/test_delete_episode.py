"""Unit coverage for the group-aware episode deletion MCP adapter."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import graphiti_mcp_server


@pytest.mark.asyncio
async def test_delete_episode_forwards_uuid_and_group_id(monkeypatch):
    client = Mock(remove_episode=AsyncMock())
    service = Mock(get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(graphiti_mcp_server, 'graphiti_service', service)

    response = await graphiti_mcp_server.delete_episode('episode-1', group_id='coding-wiki')

    assert response['message'] == 'Episode with UUID episode-1 deleted successfully'
    client.remove_episode.assert_awaited_once_with('episode-1', group_id='coding-wiki')


@pytest.mark.asyncio
async def test_delete_episode_returns_error_response_when_core_delete_fails(monkeypatch):
    client = Mock(remove_episode=AsyncMock(side_effect=RuntimeError('not found')))
    service = Mock(get_client=AsyncMock(return_value=client))
    monkeypatch.setattr(graphiti_mcp_server, 'graphiti_service', service)

    response = await graphiti_mcp_server.delete_episode('episode-1', group_id='coding-wiki')

    assert 'not found' in response['error']
