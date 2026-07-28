"""Measure what catalog grounding and verification are actually worth.

Runs the same tasks three ways against a live DataHub:

  ungrounded  - table names only, no gates      (a plain LLM writing dbt)
  grounded    - real schemas injected, no gates (context, but nobody checking)
  verified    - real schemas + the gate loop    (Groundskeeper)

"Correct" is decided by the gates on a single attempt, so the ungrounded and
grounded arms are judged by exactly the same standard the verified arm has to
meet; the verified arm is additionally allowed to repair. Results land in
examples/ so a judge can re-run this and compare.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from groundskeeper.catalog_loader import find_datasets, load_schemas, mcp_session
from groundskeeper.gates.base import Verdict
from groundskeeper.gates.compilation import CompilationGate
from groundskeeper.gates.field_existence import FieldExistenceGate
from groundskeeper.gates.governance import GovernanceGate
from groundskeeper.generator import LLMGenerator
from groundskeeper.pipeline import VerifiedGenerator
from groundskeeper.warehouse import build_warehouse

TABLES = ["orders", "order_items", "customers", "products", "regions", "addresses"]

TASKS = [
    "total revenue and order count by region for the last 30 days",
    "the ten products with the highest total quantity sold",
    "average order value per customer, highest first",
    "monthly order volume for the current year",
    "customers who have placed more than five orders",
    "revenue by product category",
    "orders that have not shipped yet, with the customer contact",
    "average number of items per order by region",
]


def make_gates(context, name_to_urn):
    warehouse = build_warehouse(context, {u: t for t, u in name_to_urn.items()})
    return [FieldExistenceGate(), GovernanceGate(), CompilationGate(warehouse)]


def run_arm(label, generator, gates, context, task, name_to_urn, max_attempts):
    runner = VerifiedGenerator(generator, gates, context, max_attempts=max_attempts)
    try:
        report = runner.run(task, name_to_urn)
    except Exception as e:
        return None, {
            "arm": label,
            "task": task,
            "outcome": "error",
            "detail": str(e)[:200],
        }
    first = report.attempts[0] if report.attempts else None
    return report, {
        "arm": label,
        "task": task,
        "outcome": report.verdict.value,
        "shipped": report.shipped,
        "attempts": len(report.attempts),
        "first_attempt_clean": bool(first and first.verdict is Verdict.PASS),
        "first_attempt_failures": [
            f"{r.gate}: {r.findings[0].message if r.findings else ''}"
            for r in (first.failures if first else [])
        ],
    }


def grounded_row_from(report, task):
    """The grounded arm IS the verified arm's first attempt -- same LLM call.

    Deriving it instead of generating separately removes sampling noise, so
    'verified' can never score below 'grounded' for reasons unrelated to repair.
    """
    first = report.attempts[0] if report and report.attempts else None
    clean = bool(first and first.verdict is Verdict.PASS)
    return {
        "arm": "grounded",
        "task": task,
        "outcome": (first.verdict.value if first else "error"),
        "shipped": clean,
        "attempts": 1,
        "first_attempt_clean": clean,
        "first_attempt_failures": [
            f"{r.gate}: {r.findings[0].message if r.findings else ''}"
            for r in (first.failures if first else [])
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=len(TASKS))
    parser.add_argument("--out", default="examples/benchmark.json")
    args = parser.parse_args()
    tasks = TASKS[: args.tasks]

    async with mcp_session() as session:
        available = await find_datasets(session)
        name_to_urn = {t: available[t] for t in TABLES if t in available}
        context = await load_schemas(session, list(name_to_urn.values()))

    print(f"catalog: {len(name_to_urn)} tables, {len(tasks)} tasks\n")
    rows = []

    for i, task in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {task}")
        gates = make_gates(context, name_to_urn)

        _, ungrounded_row = run_arm(
            "ungrounded",
            LLMGenerator(context, name_to_urn, grounded=False),
            gates,
            context,
            task,
            name_to_urn,
            1,
        )
        verified_report, verified_row = run_arm(
            "verified",
            LLMGenerator(context, name_to_urn, grounded=True),
            gates,
            context,
            task,
            name_to_urn,
            3,
        )
        rows.append(ungrounded_row)
        rows.append(grounded_row_from(verified_report, task))
        rows.append(verified_row)

        for row in rows[-3:]:
            mark = "ok " if row.get("shipped") else "NO "
            print(
                f"    {mark} {row['arm']:11} {row['outcome']:9} attempts={row.get('attempts', 0)}"
            )

    summary = {}
    for arm in ("ungrounded", "grounded", "verified"):
        subset = [r for r in rows if r["arm"] == arm]
        clean = sum(1 for r in subset if r.get("first_attempt_clean"))
        shipped = sum(1 for r in subset if r.get("shipped"))
        summary[arm] = {
            "tasks": len(subset),
            "first_attempt_clean": clean,
            "shipped": shipped,
            "first_attempt_rate": round(100 * clean / len(subset)) if subset else 0,
            "shipped_rate": round(100 * shipped / len(subset)) if subset else 0,
        }

    print("\n" + "=" * 62)
    print(f"{'arm':12} {'first-try correct':>18} {'shipped':>12}")
    for arm, s in summary.items():
        print(
            f"{arm:12} {s['first_attempt_clean']:>3}/{s['tasks']} ({s['first_attempt_rate']:>3}%)"
            f"{s['shipped']:>8}/{s['tasks']} ({s['shipped_rate']:>3}%)"
        )
    print("=" * 62)
    print("\nNothing that fails the gates is ever handed to a reviewer.")

    out = Path(__file__).resolve().parents[1] / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "runs": rows}, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
