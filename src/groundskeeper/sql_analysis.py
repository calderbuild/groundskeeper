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
from sqlglot.optimizer.scope import traverse_scope


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

    Resolution is scope-aware: an unqualified column is attributed using the
    tables in ITS OWN select, not every table in the statement. Resolving
    globally would call a column ambiguous just because some unrelated subquery
    joins two tables, and would mis-attribute columns inside a CTE.

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

    try:
        scopes = traverse_scope(tree)
    except Exception as e:
        result.parse_error = f"scope resolution failed: {type(e).__name__}: {e}"
        return result

    seen: set[tuple[str, str]] = set()
    seen_tables: set[str] = set()

    for scope in scopes:
        # Sources that are real tables; anything else (CTE, subquery) is defined
        # by the query itself and was already checked where it was built.
        local: dict[str, TableRef] = {}
        for handle, source in scope.sources.items():
            if not isinstance(source, exp.Table):
                continue
            name = _table_name(source)
            if not name:
                continue
            ref = TableRef(
                name=name, alias=source.alias or None, urn=lookup.get(name.lower())
            )
            local[handle.lower()] = ref
            local.setdefault(name.lower(), ref)
            if name not in seen_tables:
                seen_tables.add(name)
                result.tables.append(ref)
                if ref.urn is None:
                    result.unresolved_tables.append(name)

        real_urns = {r.urn for r in local.values() if r.urn}

        for col in scope.columns:
            column = col.name
            if not column or column == "*":
                continue
            qualifier = col.table

            if qualifier:
                ref = local.get(qualifier.lower())
                if ref is None:
                    # Qualified by a CTE or subquery alias, not a catalog table.
                    continue
                if ref.urn is None:
                    continue
                target = ref.urn
            elif len(real_urns) == 1:
                target = next(iter(real_urns))
            elif len(real_urns) > 1:
                # Guessing here would let a hallucinated column pass by
                # "belonging" to whichever table happens to have it.
                if column not in result.ambiguous:
                    result.ambiguous.append(column)
                continue
            else:
                continue

            pair = (target, column)
            if pair not in seen:
                seen.add(pair)
                result.column_refs.append(pair)

    return result
