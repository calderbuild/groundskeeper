# Gate verification against a live DataHub catalog

Run: `python scripts/e2e_gate_probe.py` against a local DataHub quickstart
with the `showcase-ecommerce` datapack loaded.

The schema below was read from DataHub over MCP (`list_schema_fields`),
not fixtured. The hallucinated columns in Case 2 are the kind an LLM
produces when it writes plausible SQL without checking the catalog.

```
REAL schema loaded: 11 columns
  ['condition', 'dispatch_date', 'estimated_delivery', 'gift_wrap', 'line_item_id', 'order_id', 'product_id', 'quantity', 'return_date', 'supplier_id', 'unit_price']

=== CASE 1: SQL using columns that really exist ===
[PASS] field_existence 

=== CASE 2: the hallucination an LLM actually makes ===
[FAIL] field_existence
  - Column does not exist in the catalog schema | at urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD).unit_cost | fix: remove the reference or pick a column that exists | evidence: catalog schema for urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD) has 11 columns
  - Column does not exist in the catalog schema | at urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD).ordered_at | fix: remove the reference or pick a column that exists | evidence: catalog schema for urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD) has 11 columns 

=== CASE 3: near-miss typo gets a repair suggestion ===
[FAIL] field_existence
  - Column does not exist in the catalog schema | at urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD).line_item_i | fix: did you mean `line_item_id`? | evidence: catalog schema for urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD) has 11 columns; closest: line_item_id
```
