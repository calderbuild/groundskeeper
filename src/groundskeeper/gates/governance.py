"""Governance gate: stop generated code from laundering PII into untagged outputs.

A model that reads `customers.email` and writes it into `mart_customer_summary`
has just created an untagged copy of personal data. The SQL is correct, the tests
pass, and the compliance problem is invisible until an audit finds it.

DataHub knows which source columns carry sensitive tags or glossary terms. This
gate propagates that knowledge to the generated output and refuses to ship an
artifact whose declared tags don't cover what it actually selects.
"""

from __future__ import annotations

from .base import Finding, Gate, GateResult

# Tag/term names that mark data as needing governance downstream. Matched
# case-insensitively as substrings, so "PII" catches "pii", "Contains PII", etc.
SENSITIVE_MARKERS = ("pii", "sensitive", "confidential", "personal_data", "gdpr", "phi")


def _is_sensitive(label: str) -> bool:
    low = label.lower()
    return any(marker in low for marker in SENSITIVE_MARKERS)


class GovernanceGate(Gate):
    name = "governance"

    def check(self, artifact, context) -> GateResult:
        """Compare sensitivity inherited from source columns against the artifact's own tags.

        artifact.column_refs: [(table_urn, column)]
        artifact.declared_tags: set[str] -- tags the generated asset declares
        context.tags_for_column(urn, column) -> set[str] | None
        """
        inherited: dict[str, list[str]] = {}
        unverifiable: list[Finding] = []

        for table_urn, column in artifact.column_refs:
            known = context.schema_for(table_urn)
            if known is not None and column not in known:
                # A column that doesn't exist is the field-existence gate's
                # problem. Escalating it here too would turn a repairable
                # failure into a dead end, since escalation stops the retry loop.
                continue

            tags = context.tags_for_column(table_urn, column)
            if tags is None:
                unverifiable.append(
                    Finding(
                        message="No governance metadata for this column, sensitivity cannot be verified",
                        locus=f"{table_urn}.{column}",
                        suggestion="Classify this column in DataHub before generating code that reads it",
                    )
                )
                continue
            for tag in tags:
                if _is_sensitive(tag):
                    inherited.setdefault(tag, []).append(f"{table_urn}.{column}")

        declared = {t.lower() for t in getattr(artifact, "declared_tags", set())}
        findings: list[Finding] = []

        for tag, sources in sorted(inherited.items()):
            if tag.lower() in declared:
                continue
            findings.append(
                Finding(
                    message=f"Output reads {tag}-tagged data but does not carry the tag",
                    locus=artifact.output_name,
                    suggestion=f"add the `{tag}` tag to the generated asset, or drop these columns",
                    evidence=[f"inherited from {s}" for s in sorted(sources)],
                )
            )

        if unverifiable:
            return self._escalate(unverifiable + findings)
        if findings:
            return self._fail(findings)
        return self._pass()
