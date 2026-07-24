"""Field-existence gate: the one that catches hallucinated columns.

An LLM writing SQL will happily reference `customer_region` because that is what
the column *should* be called. If it was renamed to `region_code` three weeks ago,
the model compiles in the author's head and fails in production.

This gate resolves every column reference in the generated SQL against the schema
DataHub actually holds, and rejects anything the catalog does not know about.
Near-misses become repair suggestions so the generator can fix itself.
"""

from __future__ import annotations

import difflib

from .base import Finding, Gate, GateResult

# A column the catalog doesn't know, but that no human needs to fix: SQL builtins
# and generated aliases. Kept deliberately small -- over-broad allowlisting here
# would silently re-open the exact hole this gate exists to close.
_NON_COLUMN_TOKENS = frozenset(
    {
        "select",
        "from",
        "where",
        "group",
        "order",
        "by",
        "having",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "full",
        "cross",
        "on",
        "as",
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
        "case",
        "when",
        "then",
        "else",
        "end",
        "with",
        "union",
        "all",
        "distinct",
        "limit",
        "offset",
        "asc",
        "desc",
        "sum",
        "count",
        "avg",
        "min",
        "max",
        "cast",
        "coalesce",
        "date_trunc",
        "current_date",
        "interval",
        "between",
        "like",
        "exists",
        "over",
        "partition",
        "row_number",
        "rank",
        "true",
        "false",
    }
)

# How close a catalog column must be before we call it a likely typo.
_SUGGESTION_CUTOFF = 0.72


class FieldExistenceGate(Gate):
    name = "field_existence"

    def check(self, artifact, context) -> GateResult:
        """artifact.column_refs: [(table_urn, column)]; context.schema_for(urn) -> set[str]."""
        findings: list[Finding] = []
        unknown_tables: list[Finding] = []

        for table_urn, column in artifact.column_refs:
            if column.lower() in _NON_COLUMN_TOKENS:
                continue

            known = context.schema_for(table_urn)
            if known is None:
                # No schema in the catalog: we cannot verify, so we must not pass it.
                unknown_tables.append(
                    Finding(
                        message="No schema in DataHub for this table, cannot verify its columns",
                        locus=table_urn,
                        suggestion="Ingest this dataset into DataHub, or point the model at a catalogued table",
                    )
                )
                continue

            if column in known:
                continue

            # Case-insensitive match is a real match in most warehouses, but flag
            # it so the emitted SQL matches the catalog exactly.
            ci = {c.lower(): c for c in known}
            if column.lower() in ci:
                findings.append(
                    Finding(
                        message="Column case does not match the catalog",
                        locus=f"{table_urn}.{column}",
                        suggestion=f"use `{ci[column.lower()]}`",
                        evidence=[f"catalog schema for {table_urn}"],
                    )
                )
                continue

            close = difflib.get_close_matches(
                column, sorted(known), n=3, cutoff=_SUGGESTION_CUTOFF
            )
            findings.append(
                Finding(
                    message="Column does not exist in the catalog schema",
                    locus=f"{table_urn}.{column}",
                    suggestion=(
                        f"did you mean `{close[0]}`?"
                        if close
                        else "remove the reference or pick a column that exists"
                    ),
                    evidence=[
                        f"catalog schema for {table_urn} has {len(known)} columns"
                        + (f"; closest: {', '.join(close)}" if close else "")
                    ],
                )
            )

        if unknown_tables:
            # Missing metadata is not the generator's fault -- a human decides.
            return self._escalate(unknown_tables + findings)
        if findings:
            return self._fail(findings)
        return self._pass()
