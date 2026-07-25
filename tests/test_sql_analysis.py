"""The gates only see what the analyzer extracts, so its blind spots are theirs."""

from groundskeeper.sql_analysis import analyze

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.orders,PROD)"
CUST = "urn:li:dataset:(urn:li:dataPlatform:snowflake,shop.customers,PROD)"
NAMES = {"orders": ORDERS, "customers": CUST}


def test_single_table_unqualified_columns_resolve():
    a = analyze("SELECT order_id, net_revenue FROM orders", NAMES)
    assert set(a.column_refs) == {(ORDERS, "order_id"), (ORDERS, "net_revenue")}
    assert not a.ambiguous


def test_qualified_columns_resolve_through_aliases():
    sql = "SELECT o.order_id, c.email FROM orders o JOIN customers c ON o.customer_id = c.id"
    a = analyze(sql, NAMES)
    assert (ORDERS, "order_id") in a.column_refs
    assert (CUST, "email") in a.column_refs
    # Join keys are column references too and must be checked like any other.
    assert (ORDERS, "customer_id") in a.column_refs
    assert (CUST, "id") in a.column_refs


def test_unqualified_column_in_a_join_is_ambiguous_not_guessed():
    # Attributing this to a table would let a hallucinated column pass by
    # "belonging" to whichever table happens to have it.
    sql = "SELECT mystery_col FROM orders o JOIN customers c ON o.customer_id = c.id"
    a = analyze(sql, NAMES)
    assert "mystery_col" in a.ambiguous
    assert not any(col == "mystery_col" for _, col in a.column_refs)


def test_table_missing_from_the_catalog_is_reported():
    a = analyze("SELECT x FROM nowhere_table", NAMES)
    assert "nowhere_table" in a.unresolved_tables


def test_columns_inside_expressions_are_found():
    sql = "SELECT SUM(net_revenue) AS total, DATE_TRUNC('day', ordered_at) AS d FROM orders GROUP BY d"
    a = analyze(sql, NAMES)
    cols = {c for _, c in a.column_refs}
    assert {"net_revenue", "ordered_at"} <= cols


def test_cte_columns_are_attributed_to_the_source_table():
    sql = """
        WITH recent AS (SELECT order_id, net_revenue FROM orders)
        SELECT order_id FROM recent
    """
    a = analyze(sql, NAMES)
    assert (ORDERS, "net_revenue") in a.column_refs


def test_star_is_not_a_column():
    a = analyze("SELECT * FROM orders", NAMES)
    assert not any(c == "*" for _, c in a.column_refs)


def test_broken_sql_reports_a_parse_error_instead_of_raising():
    a = analyze("SELECT FROM WHERE :::", NAMES)
    assert a.parse_error or not a.column_refs


def test_table_names_match_case_insensitively():
    a = analyze("SELECT order_id FROM ORDERS", NAMES)
    assert (ORDERS, "order_id") in a.column_refs
