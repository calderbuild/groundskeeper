"""Compilation gate: does the generated SQL actually run?

Everything upstream of this reasons about the query. This gate executes it
against tables built from the catalog's schemas, so a type error, a bad join
condition, or an aggregate used where it isn't allowed fails here instead of in
whatever the data team merges.

DuckDB's error messages are good enough to hand straight back to the generator
as a repair instruction, so we pass them through rather than paraphrasing.
"""

from __future__ import annotations

from .base import Finding, Gate, GateResult


class CompilationGate(Gate):
    name = "compilation"

    def __init__(self, connection):
        self.connection = connection

    def check(self, artifact, context) -> GateResult:
        """artifact.sql is executed with a LIMIT 0 wrapper: plan it, don't scan it."""
        sql = (artifact.sql or "").strip().rstrip(";")
        if not sql:
            return self._fail([Finding(message="Artifact contains no SQL")])

        try:
            self.connection.execute(f"SELECT * FROM ({sql}) AS _gk_probe LIMIT 0")
        except Exception as e:
            return self._fail(
                [
                    Finding(
                        message="SQL failed to execute against the catalog's schema",
                        locus=artifact.output_name,
                        suggestion=str(e).strip().splitlines()[0]
                        if str(e).strip()
                        else None,
                        evidence=[
                            "executed on DuckDB tables built from DataHub schemas"
                        ],
                    )
                ]
            )
        return self._pass()
