"""The field-existence gate is the project's core claim. These tests are the proof."""

from dataclasses import dataclass, field

from groundskeeper.gates.base import Verdict
from groundskeeper.gates.field_existence import FieldExistenceGate

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.public.orders,PROD)"
UNKNOWN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.public.ghost,PROD)"


@dataclass
class FakeArtifact:
    column_refs: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class FakeContext:
    schemas: dict[str, set[str]] = field(default_factory=dict)

    def schema_for(self, urn: str):
        return self.schemas.get(urn)


def ctx() -> FakeContext:
    return FakeContext(
        schemas={ORDERS: {"order_id", "region_code", "net_revenue", "ordered_at"}}
    )


def test_real_columns_pass():
    art = FakeArtifact(column_refs=[(ORDERS, "order_id"), (ORDERS, "net_revenue")])
    assert FieldExistenceGate().check(art, ctx()).verdict is Verdict.PASS


def test_hallucinated_column_is_caught():
    # The headline case: the model that "looks right" and is wrong.
    art = FakeArtifact(column_refs=[(ORDERS, "customer_region")])
    result = FieldExistenceGate().check(art, ctx())
    assert result.verdict is Verdict.FAIL
    assert "does not exist" in result.findings[0].message


def test_near_miss_gets_a_repair_suggestion():
    art = FakeArtifact(column_refs=[(ORDERS, "region_cod")])
    result = FieldExistenceGate().check(art, ctx())
    assert result.verdict is Verdict.FAIL
    assert "region_code" in (result.findings[0].suggestion or "")


def test_sql_keywords_are_not_treated_as_columns():
    art = FakeArtifact(
        column_refs=[(ORDERS, "sum"), (ORDERS, "where"), (ORDERS, "order_id")]
    )
    assert FieldExistenceGate().check(art, ctx()).verdict is Verdict.PASS


def test_case_mismatch_is_flagged_with_the_catalog_spelling():
    art = FakeArtifact(column_refs=[(ORDERS, "ORDER_ID")])
    result = FieldExistenceGate().check(art, ctx())
    assert result.verdict is Verdict.FAIL
    assert "order_id" in (result.findings[0].suggestion or "")


def test_uncatalogued_table_escalates_instead_of_passing():
    # Missing metadata must never be silently treated as "fine".
    art = FakeArtifact(column_refs=[(UNKNOWN, "whatever")])
    result = FieldExistenceGate().check(art, ctx())
    assert result.verdict is Verdict.ESCALATE


def test_escalation_wins_over_failure():
    art = FakeArtifact(column_refs=[(UNKNOWN, "x"), (ORDERS, "nope_not_here")])
    result = FieldExistenceGate().check(art, ctx())
    assert result.verdict is Verdict.ESCALATE
    assert len(result.findings) == 2
