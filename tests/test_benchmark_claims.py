"""The published numbers have to follow from the published runs.

The README and the submission both lead with one table: 12 percent ungrounded,
88 percent grounded, 100 percent verified. That table is the project's central
claim, and it is read by people who will not re-run an eight-task benchmark
against a live DataHub to check it. So the artifact has to be self-checking.

An earlier version of this benchmark reported the verified arm scoring *below*
the grounded arm, which cannot happen by construction, because verified is
grounded plus repair. It came from running the two arms as independent
generations and comparing across sampling noise. The invariants below are the
ones that were violated then.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "examples" / "benchmark.json"
ARMS = ("ungrounded", "grounded", "verified")


@pytest.fixture(scope="module")
def benchmark() -> dict:
    return json.loads(BENCHMARK.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_arm(benchmark) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for run in benchmark["runs"]:
        grouped[run["arm"]].append(run)
    return dict(grouped)


def test_the_summary_is_derivable_from_the_runs(benchmark, by_arm):
    """A stale summary would let the headline drift from the evidence."""
    for arm in ARMS:
        runs = by_arm[arm]
        declared = benchmark["summary"][arm]
        clean = sum(bool(r["first_attempt_clean"]) for r in runs)
        shipped = sum(bool(r["shipped"]) for r in runs)

        assert declared["tasks"] == len(runs)
        assert declared["first_attempt_clean"] == clean
        assert declared["shipped"] == shipped
        assert declared["first_attempt_rate"] == round(clean / len(runs) * 100)
        assert declared["shipped_rate"] == round(shipped / len(runs) * 100)


def test_every_arm_ran_the_same_tasks(by_arm):
    """Comparing arms across different tasks would not be a comparison."""
    baseline = [r["task"] for r in by_arm["ungrounded"]]
    assert len(set(baseline)) == len(baseline), "a task is duplicated within an arm"
    for arm in ARMS:
        assert [r["task"] for r in by_arm[arm]] == baseline


def test_verification_cannot_ship_less_than_grounding_alone(benchmark):
    """This is the invariant the earlier broken benchmark violated.

    The verified arm is the grounded arm plus a repair loop, so it can only
    ship the same models or more. Scoring below is a signal that the arms were
    generated independently and are being compared across sampling noise.
    """
    summary = benchmark["summary"]
    assert summary["verified"]["shipped"] >= summary["grounded"]["shipped"]


def test_the_grounded_arm_is_the_verified_arms_own_first_attempt(benchmark):
    """Stated in the writeup as the reason the comparison carries no noise.

    If the two arms shared one first attempt, they agree on how many first
    attempts were clean. Different numbers mean they were sampled separately
    and the claim is no longer true.
    """
    summary = benchmark["summary"]
    assert (
        summary["grounded"]["first_attempt_clean"]
        == summary["verified"]["first_attempt_clean"]
    )


def test_a_run_that_shipped_nothing_reports_why(by_arm):
    """A blocked run with no recorded failure is an unexplained rejection."""
    for arm in ARMS:
        for run in by_arm[arm]:
            if not run["first_attempt_clean"]:
                assert run["first_attempt_failures"], (
                    f"{arm}: {run['task']!r} failed its first attempt "
                    "without recording a gate verdict"
                )


def test_the_headline_numbers_are_the_ones_the_readme_prints(benchmark):
    """The three percentages quoted in the README and the submission."""
    summary = benchmark["summary"]
    assert summary["ungrounded"]["shipped_rate"] == 12
    assert summary["grounded"]["shipped_rate"] == 88
    assert summary["verified"]["shipped_rate"] == 100
