# The defect only execution finds

Task: `total revenue and order count by region for the last 30 days`

Both runs below were generated against a live DataHub (`showcase-ecommerce`) and
both hit the same defect on their first attempt:

    Binder Error: Cannot compare values of type VARCHAR and type TIMESTAMP

`orders.order_date` is stored as `VARCHAR` in this warehouse. The model wrote
`WHERE orders.order_date >= CURRENT_DATE - INTERVAL '30 days'`, which reads
perfectly and throws at runtime. No linter, no schema check, and few human
reviewers would catch it. Executing the query against tables built from the
catalog's own types catches it every time.

Both runs then repaired themselves and shipped on attempt two, adding the cast.

A single task is an anecdote and varies between runs, since the model is
sampled. For the aggregate effect of grounding, see
[`benchmark.json`](benchmark.json) and the table in the README: 1/8 without the
catalog, 8/8 with it plus verification.

## Without catalog context (table names only)

```
grounded on 5 tables: orders, order_items, customers, products, regions
  orders: 15 columns
  order_items: 11 columns
  customers: 22 columns
  products: 12 columns
  regions: 4 columns

======================================================================
# Verification report: total revenue and order count by region for the last 30 days

## Attempt 1: FAIL
[PASS] sql_analysis
[PASS] field_existence
[PASS] governance
[FAIL] compilation
  - SQL failed to execute against the catalog's schema | at generated_model | fix: Binder Error: Cannot compare values of type VARCHAR and type TIMESTAMP - an explicit cast is required | evidence: executed on DuckDB tables built from DataHub schemas

## Attempt 2: PASS
[PASS] sql_analysis
[PASS] field_existence
[PASS] governance
[PASS] compilation

**Outcome: SHIPPED** after 2 attempt(s)
======================================================================

--- final SQL ---
-- generated_model
-- total revenue and order count by region for the last 30 days

SELECT regions.region_name,
       COUNT(DISTINCT orders.order_id) AS total_orders,
       SUM(order_items.quantity * order_items.unit_price) AS total_revenue
FROM orders
JOIN customers ON orders.customer_id = customers.customer_id
JOIN regions ON customers.region_id = regions.region_id
JOIN order_items ON orders.order_id = order_items.order_id
WHERE CAST(orders.order_date AS TIMESTAMP) >= NOW() - INTERVAL '30 days'
GROUP BY regions.region_name

```

## With DataHub schemas + verification

```
grounded on 5 tables: orders, order_items, customers, products, regions
  orders: 15 columns
  order_items: 11 columns
  customers: 22 columns
  products: 12 columns
  regions: 4 columns

======================================================================
# Verification report: total revenue and order count by region for the last 30 days

## Attempt 1: FAIL
[PASS] sql_analysis
[PASS] field_existence
[PASS] governance
[FAIL] compilation
  - SQL failed to execute against the catalog's schema | at generated_model | fix: Binder Error: Cannot compare values of type VARCHAR and type TIMESTAMP - an explicit cast is required | evidence: executed on DuckDB tables built from DataHub schemas

## Attempt 2: PASS
[PASS] sql_analysis
[PASS] field_existence
[PASS] governance
[PASS] compilation

**Outcome: SHIPPED** after 2 attempt(s)
======================================================================

--- final SQL ---
-- generated_model
-- total revenue and order count by region for the last 30 days

SELECT
    r.region_name,
    COUNT(DISTINCT o.order_id) AS order_count,
    SUM(o.order_total) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN regions r ON c.region_id = r.region_id
WHERE CAST(o.order_date AS DATE) >= CURRENT_DATE - INTERVAL '30 DAY'
GROUP BY r.region_name

```
