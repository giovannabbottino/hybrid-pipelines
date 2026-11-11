"""MCP transport and tool discovery. Query planning belongs to the LLM."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ..configuration import float_env


@dataclass(frozen=True)
class WikidataMCPConfig:
    url: str = "https://wd-mcp.wmcloud.org/mcp/"
    timeout_seconds: float = 60.0
    allowed_tools: tuple[str, ...] = ("search_items", "get_statements")

    @classmethod
    def from_env(cls) -> WikidataMCPConfig:
        return cls(
            url=os.getenv("WIKIDATA_MCP_URL", cls.url),
            timeout_seconds=max(1.0, float_env("WIKIDATA_TIMEOUT_SECONDS", 60.0)),
            allowed_tools=tuple(
                name.strip()
                for name in os.getenv("WIKIDATA_MCP_TOOLS", "search_items,get_statements").split(",")
                if name.strip()
            ),
        )


class WikidataMCPClient:
    def __init__(self, config: WikidataMCPConfig):
        self.config = config

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[ClientSession]:
        # Each request owns its session; concurrent analyses never share state.
        async with (
            httpx.AsyncClient(timeout=self.config.timeout_seconds, follow_redirects=True) as http,
            streamable_http_client(self.config.url, http_client=http) as (read, write, _),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self.config.timeout_seconds)) as session,
        ):
            await session.initialize()
            yield session

    async def list_tools(self, session: ClientSession) -> list[dict[str, Any]]:
        tools = []
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                if tool.name in self.config.allowed_tools:
                    tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": tool.description or "",
                                "parameters": tool.inputSchema,
                            },
                        }
                    )
            cursor = page.nextCursor
            if not cursor:
                break
            if cursor in seen_cursors:
                raise RuntimeError("MCP returned a repeated tools/list cursor.")
            seen_cursors.add(cursor)
        if not tools:
            raise RuntimeError("The MCP server exposes none of the configured tools.")
        return tools

    def health(self) -> dict[str, Any]:
        async def check() -> list[dict[str, Any]]:
            async with asyncio.timeout(self.config.timeout_seconds):
                async with self.connect() as session:
                    return await self.list_tools(session)

        try:
            tools = asyncio.run(check())
            return {"status": "ok", "tools": [tool["function"]["name"] for tool in tools]}
        except Exception as exc:
            return {"status": "unavailable", "details": str(exc)}
