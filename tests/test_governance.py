"""PII must not get laundered into an untagged output."""

from dataclasses import dataclass, field

from groundskeeper.gates.base import Verdict
from groundskeeper.gates.governance import GovernanceGate

CUST = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.public.customers,PROD)"


@dataclass
class FakeArtifact:
    column_refs: list[tuple[str, str]] = field(default_factory=list)
    declared_tags: set[str] = field(default_factory=set)
    output_name: str = "mart_customer_summary"


@dataclass
class FakeContext:
    column_tags: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    # Columns known to the catalog but carrying no tags -> empty set, not None.
    known: set[tuple[str, str]] = field(default_factory=set)

    def schema_for(self, urn: str):
        cols = {c for u, c in self.known if u == urn}
        return cols or None

    def tags_for_column(self, urn: str, column: str):
        key = (urn, column)
        if key in self.column_tags:
            return self.column_tags[key]
        if key in self.known:
            return set()
        return None


def ctx() -> FakeContext:
    return FakeContext(
        column_tags={(CUST, "email"): {"PII"}, (CUST, "ssn"): {"PII", "Confidential"}},
        known={
            (CUST, "customer_id"),
            (CUST, "signup_date"),
            (CUST, "email"),
            (CUST, "ssn"),
        },
    )


def test_non_sensitive_columns_pass():
    art = FakeArtifact(column_refs=[(CUST, "customer_id"), (CUST, "signup_date")])
    assert GovernanceGate().check(art, ctx()).verdict is Verdict.PASS


def test_pii_leaking_into_untagged_output_is_caught():
    art = FakeArtifact(column_refs=[(CUST, "customer_id"), (CUST, "email")])
    result = GovernanceGate().check(art, ctx())
    assert result.verdict is Verdict.FAIL
    assert "PII" in result.findings[0].message


def test_declaring_the_inherited_tag_passes():
    art = FakeArtifact(column_refs=[(CUST, "email")], declared_tags={"PII"})
    assert GovernanceGate().check(art, ctx()).verdict is Verdict.PASS


def test_every_missing_tag_is_reported():
    art = FakeArtifact(column_refs=[(CUST, "ssn")], declared_tags={"PII"})
    result = GovernanceGate().check(art, ctx())
    assert result.verdict is Verdict.FAIL
    assert any("Confidential" in f.message for f in result.findings)


def test_finding_names_the_source_column_as_evidence():
    art = FakeArtifact(column_refs=[(CUST, "email")])
    result = GovernanceGate().check(art, ctx())
    assert any("email" in e for e in result.findings[0].evidence)


def test_uncatalogued_table_escalates():
    # Governance can't be reasoned about for a table DataHub has never seen.
    unknown = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.public.ghost,PROD)"
    art = FakeArtifact(column_refs=[(unknown, "anything")])
    assert GovernanceGate().check(art, ctx()).verdict is Verdict.ESCALATE


def test_nonexistent_column_is_left_to_the_field_existence_gate():
    # Escalating here would convert a repairable failure into a dead end.
    art = FakeArtifact(column_refs=[(CUST, "not_a_real_column")])
    assert GovernanceGate().check(art, ctx()).verdict is Verdict.PASS
