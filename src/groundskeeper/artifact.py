"""What the agent produces and hands to the gates."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Artifact:
    """A generated dbt model: the SQL, what it will be called, and its metadata.

    column_refs is filled in by the analyzer rather than the generator, so the
    gates check what the SQL actually references, not what the model claims it
    references.
    """

    sql: str
    output_name: str
    declared_tags: set[str] = field(default_factory=set)
    description: str = ""
    column_refs: list[tuple[str, str]] = field(default_factory=list)
    # Table name as written in the SQL -> dataset URN, resolved from the catalog.
    source_tables: dict[str, str] = field(default_factory=dict)

    def to_dbt_sql(self) -> str:
        header = f"-- {self.output_name}"
        if self.description:
            header += f"\n-- {self.description}"
        return f"{header}\n\n{self.sql.strip()}\n"
