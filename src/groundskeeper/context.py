"""Catalog context: what the gates check generated code against.

Everything here comes from DataHub over MCP. Nothing is inferred, defaulted, or
guessed -- when the catalog doesn't know something, we return None and let the
gate escalate. A gate that silently assumes a column exists is worse than no gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class CatalogContext:
    """Schemas and governance tags pulled from DataHub, keyed by dataset URN."""

    schemas: dict[str, set[str]] = field(default_factory=dict)
    field_types: dict[str, dict[str, str]] = field(default_factory=dict)
    column_tags: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def schema_for(self, urn: str) -> set[str] | None:
        """Columns DataHub knows for this dataset. None means 'not catalogued'."""
        return self.schemas.get(urn)

    def type_of(self, urn: str, column: str) -> str | None:
        return self.field_types.get(urn, {}).get(column)

    def tags_for_column(self, urn: str, column: str) -> set[str] | None:
        """Governance tags on a column. None means 'unknown', empty set means 'none'."""
        if urn not in self.schemas:
            return None
        if column not in self.schemas[urn]:
            return None
        return self.column_tags.get((urn, column), set())

    def add_dataset(self, urn: str, fields_payload: dict) -> None:
        """Ingest one `list_schema_fields` response."""
        cols: set[str] = set()
        types: dict[str, str] = {}
        for f in fields_payload.get("fields", []):
            path = f.get("fieldPath")
            if not path:
                continue
            cols.add(path)
            if f.get("nativeDataType"):
                types[path] = f["nativeDataType"]
            for tag in _extract_tags(f):
                self.column_tags.setdefault((urn, path), set()).add(tag)
        self.schemas[urn] = cols
        self.field_types[urn] = types


def _extract_tags(field_payload: dict) -> list[str]:
    """Pull tag and glossary-term names off a schema field, tolerating shape drift.

    DataHub returns these under a few different shapes depending on version and
    query; we read the ones we know and ignore anything unrecognised rather than
    crashing a verification run over a payload detail.
    """
    names: list[str] = []
    for container, key in (("globalTags", "tags"), ("glossaryTerms", "terms")):
        block = field_payload.get(container) or {}
        for item in block.get(key, []) or []:
            for path in (
                ("tag", "name"),
                ("tag", "urn"),
                ("term", "name"),
                ("term", "urn"),
            ):
                node = item
                for part in path:
                    node = (node or {}).get(part) if isinstance(node, dict) else None
                if isinstance(node, str) and node:
                    names.append(node.split(":")[-1])
                    break
    return names


async def load_context(mcp_client, urns: list[str]) -> CatalogContext:
    """Build a CatalogContext by calling list_schema_fields for each dataset."""
    ctx = CatalogContext()
    for urn in urns:
        result = await mcp_client.call_tool("list_schema_fields", {"urn": urn})
        ctx.add_dataset(urn, json.loads(result.content[0].text))
    return ctx
