"""A throwaway DuckDB warehouse built from the catalog's own schemas.

Checking SQL against a parser only proves it parses. Running it against tables
whose columns and types come from DataHub proves the query is executable on the
shape of data it claims to read -- type mismatches, bad joins, and invalid
aggregate usage all surface here and nowhere earlier.

The tables are empty. We are verifying that the query *can* run, not what it
returns, so seeding rows would cost time and prove nothing extra.
"""

from __future__ import annotations

import re

import duckdb

from .context import CatalogContext

# Warehouse-native types mapped onto DuckDB equivalents. Anything unrecognised
# becomes VARCHAR: permissive on purpose, since a wrong guess here would fail a
# query for a reason that has nothing to do with the generated SQL.
_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"^(VARCHAR|STRING|TEXT|CHAR|NVARCHAR)", "VARCHAR"),
    (r"^(NUMBER|NUMERIC|DECIMAL)", "DECIMAL(38,9)"),
    (r"^(BIGINT|INT8)", "BIGINT"),
    (r"^(INT|INTEGER|SMALLINT|TINYINT)", "INTEGER"),
    (r"^(FLOAT|DOUBLE|REAL)", "DOUBLE"),
    (r"^(BOOLEAN|BOOL)", "BOOLEAN"),
    (r"^TIMESTAMP", "TIMESTAMP"),
    (r"^DATE", "DATE"),
    (r"^TIME", "TIME"),
    (r"^(JSON|VARIANT|OBJECT)", "JSON"),
    (r"^(ARRAY)", "VARCHAR[]"),
]


def duckdb_type(native: str | None) -> str:
    if not native:
        return "VARCHAR"
    upper = native.strip().upper()
    for pattern, mapped in _TYPE_PATTERNS:
        if re.match(pattern, upper):
            return mapped
    return "VARCHAR"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def build_warehouse(
    context: CatalogContext, urn_to_table: dict[str, str]
) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB whose tables mirror the catalog's schemas.

    urn_to_table maps a dataset URN to the table name the SQL will use.
    """
    conn = duckdb.connect(":memory:")
    for urn, table in urn_to_table.items():
        columns = context.schema_for(urn)
        if not columns:
            continue
        cols = ", ".join(
            f"{_quote(c)} {duckdb_type(context.type_of(urn, c))}"
            for c in sorted(columns)
        )
        conn.execute(f"CREATE TABLE {_quote(table)} ({cols})")
    return conn
