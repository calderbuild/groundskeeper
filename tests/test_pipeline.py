"""The repair loop is the product. These tests are its proof."""

from groundskeeper.artifact import Artifact
from groundskeeper.context import CatalogContext
from groundskeeper.gates.base import Verdict
from groundskeeper.gates.compilation import CompilationGate
from groundskeeper.gates.field_existence import FieldExistenceGate
from groundskeeper.pipeline import VerifiedGenerator
from groundskeeper.warehouse import build_warehouse

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.orders,PROD)"
NAMES = {"orders": ORDERS}


def make_context() -> CatalogContext:
    ctx = CatalogContext()
    ctx.add_dataset(
        ORDERS,
        {
            "fields": [
                {"fieldPath": "order_id", "nativeDataType": "NUMBER(38,0)"},
                {"fieldPath": "region_code", "nativeDataType": "VARCHAR(16777216)"},
                {"fieldPath": "net_revenue", "nativeDataType": "FLOAT"},
                {"fieldPath": "ordered_at", "nativeDataType": "TIMESTAMP_NTZ"},
            ]
        },
    )
    return ctx


def build_gates(ctx: CatalogContext):
    conn = build_warehouse(ctx, {ORDERS: "orders"})
    return [FieldExistenceGate(), CompilationGate(conn)]


def scripted(*sqls):
    """A generator that emits the given SQL in order, one per attempt."""
    calls = {"n": 0}

    def gen(task, brief):
        i = min(calls["n"], len(sqls) - 1)
        calls["n"] += 1
        return Artifact(sql=sqls[i], output_name="mart_revenue_by_region")

    gen.calls = calls
    return gen


def test_clean_sql_ships_on_the_first_attempt():
    ctx = make_context()
    gen = scripted(
        "SELECT region_code, SUM(net_revenue) AS revenue FROM orders GROUP BY region_code"
    )
    report = VerifiedGenerator(gen, build_gates(ctx), ctx).run(
        "revenue by region", NAMES
    )
    assert report.shipped
    assert len(report.attempts) == 1


def test_hallucinated_column_is_caught_then_repaired():
    # Attempt 1 invents `customer_region`; attempt 2 uses the real column.
    ctx = make_context()
    gen = scripted(
        "SELECT customer_region, SUM(net_revenue) AS revenue FROM orders GROUP BY customer_region",
        "SELECT region_code, SUM(net_revenue) AS revenue FROM orders GROUP BY region_code",
    )
    report = VerifiedGenerator(gen, build_gates(ctx), ctx).run(
        "revenue by region", NAMES
    )

    assert len(report.attempts) == 2
    assert report.attempts[0].verdict is Verdict.FAIL
    assert report.shipped, "the repaired artifact should ship"


def test_the_repair_brief_names_the_offending_column():
    ctx = make_context()
    gen = scripted("SELECT customer_region FROM orders")
    report = VerifiedGenerator(gen, build_gates(ctx), ctx, max_attempts=1).run(
        "x", NAMES
    )
    brief = report.attempts[0].repair_brief()
    assert "customer_region" in brief


def test_broken_sql_never_ships():
    ctx = make_context()
    gen = scripted("SELECT region_code FROM orders WHERE")
    report = VerifiedGenerator(gen, build_gates(ctx), ctx, max_attempts=2).run(
        "x", NAMES
    )
    assert not report.shipped


def test_type_error_is_caught_by_execution_not_by_reading():
    # Every column exists, so only actually running it reveals the problem.
    ctx = make_context()
    gen = scripted("SELECT SUM(region_code) AS oops FROM orders")
    report = VerifiedGenerator(gen, build_gates(ctx), ctx, max_attempts=1).run(
        "x", NAMES
    )
    assert not report.shipped
    assert any(r.gate == "compilation" for r in report.attempts[0].failures)


def test_uncatalogued_table_escalates_and_stops_retrying():
    ctx = make_context()
    gen = scripted("SELECT anything FROM some_unknown_table")
    report = VerifiedGenerator(gen, build_gates(ctx), ctx, max_attempts=3).run(
        "x", NAMES
    )
    assert report.verdict is Verdict.ESCALATE
    assert len(report.attempts) == 1, (
        "escalation must not burn retries on missing metadata"
    )


def test_report_renders_the_evidence_trail():
    ctx = make_context()
    gen = scripted(
        "SELECT customer_region FROM orders",
        "SELECT region_code FROM orders",
    )
    report = VerifiedGenerator(gen, build_gates(ctx), ctx).run(
        "revenue by region", NAMES
    )
    text = report.render()
    assert "Attempt 1" in text and "Attempt 2" in text
    assert "SHIPPED" in text
