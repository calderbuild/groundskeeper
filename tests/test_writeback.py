"""What we put in the graph must be derived from the artifact, never invented."""

from groundskeeper.artifact import Artifact
from groundskeeper.context import CatalogContext
from groundskeeper.writeback import make_urn, plan_writeback

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.orders,PROD)"
CUST = "urn:li:dataset:(urn:li:dataPlatform:dbt,shop.customers,PROD)"


def ctx() -> CatalogContext:
    c = CatalogContext()
    c.add_dataset(
        ORDERS, {"fields": [{"fieldPath": "order_id"}, {"fieldPath": "total"}]}
    )
    c.add_dataset(CUST, {"fields": [{"fieldPath": "email"}, {"fieldPath": "id"}]})
    c.column_tags[(CUST, "email")] = {"PII"}
    return c


def artifact(refs, name="mart_orders") -> Artifact:
    return Artifact(
        sql="SELECT 1", output_name=name, description="a model", column_refs=refs
    )


def test_upstreams_are_the_tables_the_sql_actually_reads():
    plan = plan_writeback(
        artifact([(ORDERS, "order_id"), (CUST, "id")]), ctx(), attempts=1
    )
    assert set(plan.upstreams) == {ORDERS, CUST}


def test_a_table_read_twice_produces_one_upstream():
    plan = plan_writeback(
        artifact([(ORDERS, "order_id"), (ORDERS, "total")]), ctx(), attempts=1
    )
    assert plan.upstreams == [ORDERS]


def test_column_lineage_has_an_edge_per_source_column():
    plan = plan_writeback(
        artifact([(ORDERS, "order_id"), (CUST, "id")]), ctx(), attempts=1
    )
    assert len(plan.column_lineage) == 2


def test_sensitivity_is_inherited_from_source_columns():
    # Reading a PII column makes the output PII. That propagation is the point.
    plan = plan_writeback(artifact([(CUST, "email")]), ctx(), attempts=1)
    assert plan.tags == ["PII"]


def test_no_tags_are_invented_for_ordinary_columns():
    plan = plan_writeback(artifact([(ORDERS, "order_id")]), ctx(), attempts=1)
    assert plan.tags == []


def test_description_records_that_it_was_verified():
    plan = plan_writeback(artifact([(ORDERS, "order_id")]), ctx(), attempts=2)
    assert "verified" in plan.description
    assert "2 attempt" in plan.description


def test_urn_is_well_formed():
    assert make_urn("mart_x") == "urn:li:dataset:(urn:li:dataPlatform:dbt,mart_x,PROD)"
