"""Load catalog context from a live DataHub over MCP.

Kept apart from the gates so they stay testable without a running DataHub, and
so the MCP wiring lives in exactly one place. The MCP server needs
register_all_tools() called explicitly -- importing the module registers nothing,
which fails silently as "0 tools exposed".
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from .context import CatalogContext


@asynccontextmanager
async def mcp_session(gms_url: str | None = None, token: str | None = None):
    """Yield a connected MCP client bound to a DataHub instance."""
    from datahub.sdk.main_client import DataHubClient
    from fastmcp import Client

    from mcp_server_datahub.mcp_server import (
        mcp,
        register_all_tools,
        with_datahub_client,
    )

    url = gms_url or os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")
    register_all_tools(is_oss=True)
    client = DataHubClient(
        server=url, token=token or os.environ.get("DATAHUB_GMS_TOKEN") or None
    )
    with with_datahub_client(client):
        async with Client(mcp) as session:
            yield session


async def find_datasets(session, query: str = "*", limit: int = 60) -> dict[str, str]:
    """Map bare table name -> dataset URN for everything matching the query."""
    result = await session.call_tool("search", {"query": query, "num_results": limit})
    payload = json.loads(result.content[0].text)
    mapping: dict[str, str] = {}
    for row in payload.get("searchResults", []):
        urn = row["entity"]["urn"]
        if not urn.startswith("urn:li:dataset:("):
            continue
        inner = urn[len("urn:li:dataset:(") : -1]
        parts = inner.split(",")
        if len(parts) < 2:
            continue
        table = parts[1].split(".")[-1]
        # First writer wins: a name resolving to two platforms is ambiguous, and
        # silently overwriting would point generated SQL at the wrong dataset.
        mapping.setdefault(table.lower(), urn)
    return mapping


async def load_schemas(session, urns: list[str]) -> CatalogContext:
    context = CatalogContext()
    for urn in urns:
        result = await session.call_tool("list_schema_fields", {"urn": urn})
        context.add_dataset(urn, json.loads(result.content[0].text))
    return context
