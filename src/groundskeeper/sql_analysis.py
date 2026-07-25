"""Turn generated SQL into the column references the gates check.

The gates are only as good as what we feed them. A gate that receives a
hand-curated list of columns proves nothing; it has to see every column the
query actually touches, attributed to the right table.

sqlglot parses; we resolve each column to a dataset URN using the FROM/JOIN
clauses and the catalog. Columns we cannot attribute confidently are reported
as ambiguous rather than guessed at -- the caller escalates instead of pretending.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp


@dataclass
class TableRef:
    """A table named in the query, with the alias it was given (if any)."""

    name: str
    alias: str | None = None
    urn: str | None = None

    @property
    def handle(self) -> str:
        return self.alias or self.name


@dataclass
class SqlAnalysis:
    tables: list[TableRef] = field(default_factory=list)
    column_refs: list[tuple[str, str]] = field(default_factory=list)
    # Columns we could not pin to one table, e.g. an unqualified name in a join.
    ambiguous: list[str] = field(default_factory=list)
    # Table names in the SQL that the catalog has no URN for.
    unresolved_tables: list[str] = field(default_factory=list)
    parse_error: str | None = None


def _table_name(node: exp.Table) -> str:
    """Bare table name, ignoring catalog/schema qualifiers."""
    return node.name


def analyze(
    sql: str, name_to_urn: dict[str, str], dialect: str = "duckdb"
) -> SqlAnalysis:
    """Extract (dataset_urn, column) pairs from SQL.

    name_to_urn maps a table name as written in the SQL to its DataHub URN.
    Lookup is case-insensitive because warehouses disagree about case.
    """
    result = SqlAnalysis()
    lookup = {k.lower(): v for k, v in name_to_urn.items()}

    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:  # sqlglot raises several types; the message is what matters
        result.parse_error = f"{type(e).__name__}: {e}"
        return result
    if tree is None:
        result.parse_error = "empty statement"
        return result

    by_handle: dict[str, TableRef] = {}
    for node in tree.find_all(exp.Table):
        name = _table_name(node)
        if not name:
            continue
        ref = TableRef(
            name=name, alias=node.alias or None, urn=lookup.get(name.lower())
        )
        if ref.urn is None and name not in result.unresolved_tables:
            result.unresolved_tables.append(name)
        result.tables.append(ref)
        by_handle[ref.handle.lower()] = ref
        # A qualified column may use the bare table name even when an alias exists.
        by_handle.setdefault(ref.name.lower(), ref)

    resolvable_urns: list[str] = [t.urn for t in result.tables if t.urn]
    seen: set[tuple[str, str]] = set()

    for col in tree.find_all(exp.Column):
        column = col.name
        if not column or column == "*":
            continue
        qualifier = col.table

        if qualifier:
            ref = by_handle.get(qualifier.lower())
            if ref is None or ref.urn is None:
                if qualifier not in result.unresolved_tables:
                    result.unresolved_tables.append(qualifier)
                continue
            target = ref.urn
        elif len(resolvable_urns) == 1:
            # Single source: an unqualified column can only come from there.
            target = resolvable_urns[0]
        else:
            # Multiple sources and no qualifier. Guessing here would let a
            # hallucinated column slip through by "belonging" to whichever
            # table happens to have it.
            if column not in result.ambiguous:
                result.ambiguous.append(column)
            continue

        pair = (target, column)
        if pair not in seen:
            seen.add(pair)
            result.column_refs.append(pair)

    return result
