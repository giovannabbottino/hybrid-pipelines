import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import ListToolsResult, Tool

from hybrid_pipelines.infrastructure.wikidata_client import WikidataMCPClient, WikidataMCPConfig


def tool(name):
    return Tool(
        name=name,
        description="Server description",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def test_discovers_paginated_tool_schemas_and_filters_by_configuration():
    session = MagicMock()
    session.list_tools = AsyncMock(
        side_effect=[
            ListToolsResult(tools=[tool("search_items"), tool("delete_item")], nextCursor="page2"),
            ListToolsResult(tools=[tool("get_statements")]),
        ]
    )
    result = asyncio.run(WikidataMCPClient(WikidataMCPConfig()).list_tools(session))
    assert [item["function"]["name"] for item in result] == ["search_items", "get_statements"]
    assert result[0]["function"]["parameters"] == tool("search_items").inputSchema
    assert session.list_tools.await_args_list[1].kwargs == {"cursor": "page2"}


@pytest.mark.parametrize(
    "pages",
    [
        [ListToolsResult(tools=[])],
        [ListToolsResult(tools=[tool("search_items")], nextCursor="same")] * 2,
    ],
)
def test_empty_or_repeated_tool_pages_fail(pages):
    session = MagicMock()
    session.list_tools = AsyncMock(side_effect=pages)
    with pytest.raises(RuntimeError):
        asyncio.run(WikidataMCPClient(WikidataMCPConfig()).list_tools(session))


def test_sdk_session_is_initialized_and_closed(monkeypatch):
    session = MagicMock()
    session.initialize = AsyncMock()
    lifecycle = []

    @asynccontextmanager
    async def transport(url, http_client):
        lifecycle.append("transport_open")
        yield "read", "write", None
        lifecycle.append("transport_closed")

    @asynccontextmanager
    async def client_session(read, write, read_timeout_seconds):
        assert (read, write) == ("read", "write")
        lifecycle.append("session_open")
        yield session
        lifecycle.append("session_closed")

    monkeypatch.setattr("hybrid_pipelines.infrastructure.wikidata_client.streamable_http_client", transport)
    monkeypatch.setattr("hybrid_pipelines.infrastructure.wikidata_client.ClientSession", client_session)

    async def run():
        async with WikidataMCPClient(WikidataMCPConfig()).connect() as connected:
            assert connected is session
            session.initialize.assert_awaited_once()

    asyncio.run(run())
    assert lifecycle == ["transport_open", "session_open", "session_closed", "transport_closed"]


def test_configurable_tools_and_timeout(monkeypatch):
    monkeypatch.setenv("WIKIDATA_MCP_TOOLS", " search_items, get_statements, ")
    monkeypatch.setenv("WIKIDATA_TIMEOUT_SECONDS", "0")
    config = WikidataMCPConfig.from_env()
    assert config.allowed_tools == ("search_items", "get_statements")
    assert config.timeout_seconds == 1
