"""Generate one dbt model against a live DataHub, with and without grounding.

Usage:
    python scripts/run_task.py "revenue by region for the last 30 days"
    python scripts/run_task.py --ungrounded "..."   # no catalog context

Requires a DataHub at DATAHUB_GMS_URL (default localhost:8080) and an
OpenAI-compatible key in GROUNDSKEEPER_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from groundskeeper.catalog_loader import find_datasets, load_schemas, mcp_session
from groundskeeper.gates.compilation import CompilationGate
from groundskeeper.gates.field_existence import FieldExistenceGate
from groundskeeper.gates.governance import GovernanceGate
from groundskeeper.generator import LLMGenerator
from groundskeeper.pipeline import VerifiedGenerator
from groundskeeper.warehouse import build_warehouse

DEFAULT_TABLES = ["orders", "order_items", "customers", "products", "regions"]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--tables", nargs="*", default=DEFAULT_TABLES)
    parser.add_argument(
        "--ungrounded", action="store_true", help="withhold catalog schemas"
    )
    parser.add_argument("--no-gates", action="store_true", help="skip verification")
    parser.add_argument("--name", default="generated_model")
    args = parser.parse_args()

    async with mcp_session() as session:
        available = await find_datasets(session)
        name_to_urn = {t: available[t] for t in args.tables if t in available}
        missing = [t for t in args.tables if t not in available]
        if missing:
            print(f"not in catalog, skipping: {', '.join(missing)}")
        if not name_to_urn:
            print("No requested tables found in the catalog.")
            return 1
        context = await load_schemas(session, list(name_to_urn.values()))

    print(f"grounded on {len(name_to_urn)} tables: {', '.join(name_to_urn)}")
    for table, urn in name_to_urn.items():
        print(f"  {table}: {len(context.schema_for(urn) or ())} columns")

    generator = LLMGenerator(
        context, name_to_urn, output_name=args.name, grounded=not args.ungrounded
    )
    gates = []
    if not args.no_gates:
        warehouse = build_warehouse(context, {u: t for t, u in name_to_urn.items()})
        gates = [FieldExistenceGate(), GovernanceGate(), CompilationGate(warehouse)]

    report = VerifiedGenerator(generator, gates, context).run(args.task, name_to_urn)

    print("\n" + "=" * 70)
    print(report.render())
    print("=" * 70)
    final = report.final
    if final:
        print("\n--- final SQL ---")
        print(final.artifact.to_dbt_sql())
    return 0 if report.shipped else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
