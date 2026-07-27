"""The run record has to survive the trip to disk and back.

A permalink that disagrees with what was on screen is worse than no permalink,
so these tests fold real event payloads into a record the same way the server
does and check what comes back out.
"""

from __future__ import annotations

import json

import pytest

from groundskeeper import store
from groundskeeper.events import Event
from groundskeeper.server import _absorb


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


def _fold(record, events):
    for kind, data in events:
        _absorb(record, Event(kind, data))


def test_absorbs_a_repaired_run_into_one_record():
    record = store.start("revenue by region", grounded=True, write_back=False)
    _fold(
        record,
        [
            ("catalog", {"tables": [{"name": "orders", "columns": 15}]}),
            ("attempt_start", {"attempt": 1, "repairing": False}),
            ("generated", {"attempt": 1, "sql": "SELECT 1"}),
            (
                "gate",
                {
                    "attempt": 1,
                    "gate": "field_existence",
                    "verdict": "pass",
                    "findings": [],
                },
            ),
            (
                "gate",
                {
                    "attempt": 1,
                    "gate": "compilation",
                    "verdict": "fail",
                    "findings": [{"message": "Binder Error", "suggestion": "cast it"}],
                },
            ),
            ("attempt_end", {"attempt": 1, "verdict": "fail"}),
            ("attempt_start", {"attempt": 2, "repairing": True}),
            ("generated", {"attempt": 2, "sql": "SELECT CAST(1 AS INT)"}),
            (
                "gate",
                {
                    "attempt": 2,
                    "gate": "compilation",
                    "verdict": "pass",
                    "findings": [],
                },
            ),
            ("attempt_end", {"attempt": 2, "verdict": "pass"}),
            (
                "run_end",
                {
                    "verdict": "pass",
                    "shipped": True,
                    "attempts": 2,
                    "sql": "SELECT CAST(1 AS INT)",
                },
            ),
        ],
    )

    assert record.shipped is True
    assert [a.n for a in record.attempts] == [1, 2]
    assert record.attempts[0].verdict == "fail"
    assert record.attempts[1].repairing is True
    assert record.final_sql == "SELECT CAST(1 AS INT)"

    failed = [g for g in record.attempts[0].gates if g.verdict == "fail"]
    assert [g.gate for g in failed] == ["compilation"]
    assert failed[0].findings[0]["suggestion"] == "cast it"


def test_saved_record_round_trips():
    record = store.start("revenue by region", grounded=False, write_back=True)
    _fold(
        record,
        [
            ("attempt_start", {"attempt": 1, "repairing": False}),
            (
                "gate",
                {"attempt": 1, "gate": "governance", "verdict": "pass", "findings": []},
            ),
            ("attempt_end", {"attempt": 1, "verdict": "pass"}),
            (
                "run_end",
                {"verdict": "pass", "shipped": True, "attempts": 1, "sql": "SELECT 1"},
            ),
            (
                "written_back",
                {"urn": "urn:li:dataset:(x,y,PROD)", "upstreams": 3, "tags": ["pii"]},
            ),
        ],
    )
    store.save(record)

    loaded = store.load(record.id)
    assert loaded is not None
    assert loaded["task"] == "revenue by region"
    assert loaded["grounded"] is False
    assert loaded["writeback"]["upstreams"] == 3
    assert loaded["attempts"][0]["gates"][0]["gate"] == "governance"
    # The record is plain JSON, so a permalink can be read without the app.
    assert json.dumps(loaded)


def test_an_error_run_is_still_recorded():
    record = store.start("something impossible", grounded=True, write_back=False)
    _fold(record, [("error", {"message": "RuntimeError: no catalog"})])
    store.save(record)

    assert store.load(record.id)["verdict"] == "error"
    assert store.recent()[0]["verdict"] == "error"


def test_recent_is_newest_first_and_summaries_omit_the_heavy_fields():
    for task in ("first", "second", "third"):
        record = store.start(task, grounded=True, write_back=False)
        _fold(
            record,
            [
                ("attempt_start", {"attempt": 1, "repairing": False}),
                ("generated", {"attempt": 1, "sql": "SELECT " + task}),
                (
                    "run_end",
                    {"verdict": "pass", "shipped": True, "attempts": 1, "sql": "x"},
                ),
            ],
        )
        store.save(record)

    runs = store.recent()
    assert [r["task"] for r in runs] == ["third", "second", "first"]
    assert "final_sql" not in runs[0], (
        "listing a hundred runs must not carry every query"
    )
    assert store.tally(runs) == {
        "total": 3,
        "shipped": 3,
        "blocked": 0,
        "escalated": 0,
        "first_try": 3,
    }


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "..", "r-2026/../etc"])
def test_a_run_id_can_never_shape_a_path(bad):
    assert store.load(bad) is None


def test_missing_run_reads_as_absent_not_a_crash():
    assert store.load("r-20260101-000000-dead") is None
