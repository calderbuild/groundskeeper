"""END-TO-END: does the gate catch a hallucinated column using REAL DataHub schema?"""

import asyncio
import os
import sys
from dataclasses import dataclass, field as dcfield
from fastmcp import Client

sys.path.insert(0, "groundskeeper/src")
from groundskeeper.context import load_context
from groundskeeper.gates.field_existence import FieldExistenceGate

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.order_entry.order_items,PROD)"


@dataclass
class Artifact:
    column_refs: list = dcfield(default_factory=list)
    output_name: str = "mart_order_summary"


async def main():
    from mcp_server_datahub.mcp_server import (
        mcp,
        register_all_tools,
        with_datahub_client,
    )
    from datahub.sdk.main_client import DataHubClient

    register_all_tools(is_oss=True)
    client = DataHubClient(server=os.environ["DATAHUB_GMS_URL"], token=None)
    with with_datahub_client(client):
        async with Client(mcp) as c:
            ctx = await load_context(c, [URN])
            print(f"REAL schema loaded: {len(ctx.schema_for(URN))} columns")
            print(f"  {sorted(ctx.schema_for(URN))}\n")

            gate = FieldExistenceGate()

            print("=== CASE 1: SQL using columns that really exist ===")
            good = Artifact(
                column_refs=[(URN, "order_id"), (URN, "quantity"), (URN, "unit_price")]
            )
            print(gate.check(good, ctx).render(), "\n")

            print("=== CASE 2: the hallucination an LLM actually makes ===")
            # 'unit_cost' and 'ordered_at' sound right; the catalog says otherwise.
            bad = Artifact(
                column_refs=[(URN, "order_id"), (URN, "unit_cost"), (URN, "ordered_at")]
            )
            print(gate.check(bad, ctx).render(), "\n")

            print("=== CASE 3: near-miss typo gets a repair suggestion ===")
            typo = Artifact(column_refs=[(URN, "line_item_i")])
            print(gate.check(typo, ctx).render())


asyncio.run(main())
