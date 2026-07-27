"""Durable record of every run.

A verification tool that forgets what it verified is a demo. Each run is written
to one JSON file under `runs/`, which is enough to serve history, permalinks and
an evidence record without a database.

Standard library only, same constraint as the server.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

RUNS_DIR = Path(
    os.environ.get("GROUNDSKEEPER_RUNS_DIR")
    or Path(__file__).resolve().parents[2] / "runs"
)

_ID_SAFE = re.compile(r"\A[a-z0-9-]+\Z")


@dataclass
class GateResult:
    gate: str
    verdict: str
    findings: list[dict] = field(default_factory=list)


@dataclass
class AttemptRecord:
    n: int
    verdict: str = "running"
    sql: str = ""
    repairing: bool = False
    gates: list[GateResult] = field(default_factory=list)


@dataclass
class RunRecord:
    id: str
    task: str
    started: str
    grounded: bool = True
    write_back: bool = False
    verdict: str = "running"
    shipped: bool = False
    catalog: list[dict] = field(default_factory=list)
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_sql: str = ""
    writeback: dict | None = None
    error: str | None = None
    duration_ms: int = 0

    def attempt(self, n: int) -> AttemptRecord:
        for a in self.attempts:
            if a.n == n:
                return a
        rec = AttemptRecord(n=n)
        self.attempts.append(rec)
        return rec

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """The shape the history rail needs. Deliberately excludes SQL and
        findings so listing a hundred runs stays cheap."""
        return {
            "id": self.id,
            "task": self.task,
            "started": self.started,
            "grounded": self.grounded,
            "verdict": self.verdict,
            "shipped": self.shipped,
            "attempts": len(self.attempts),
            "wrote_back": self.writeback is not None,
            "duration_ms": self.duration_ms,
        }


_last_stamp = ""
_stamp_lock = threading.Lock()


def new_id(now: datetime | None = None) -> str:
    """A run id that sorts chronologically as a string.

    `recent()` orders history by sorting filenames, so the timestamp has to be
    strictly increasing. The clock alone does not promise that: two runs started
    inside the same tick would sort on the random suffix, which is no order at
    all. Rather than assume a resolution is fine enough, the stamp is bumped
    until it is greater than the last one issued.
    """
    global _last_stamp
    now = now or datetime.now(timezone.utc)
    with _stamp_lock:
        # 20 digits, fixed width, so string order and numeric order agree and a
        # collision can be resolved by simply adding one.
        stamp = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond:06d}"
        if stamp <= _last_stamp:
            stamp = f"{int(_last_stamp) + 1:020d}"
        _last_stamp = stamp
    readable = f"{stamp[:8]}-{stamp[8:14]}-{stamp[14:]}"
    return f"r-{readable}-{os.urandom(2).hex()}"


def start(task: str, grounded: bool, write_back: bool) -> RunRecord:
    now = datetime.now(timezone.utc)
    return RunRecord(
        id=new_id(now),
        task=task,
        started=now.isoformat(timespec="milliseconds"),
        grounded=grounded,
        write_back=write_back,
    )


def save(record: RunRecord) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.id}.json"
    # Write-then-rename so a reader never sees a half-written record.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record.to_dict(), indent=1), encoding="utf-8")
    tmp.replace(path)


def load(run_id: str) -> dict | None:
    if not _ID_SAFE.match(run_id):
        return None  # never let a request shape a filesystem path
    path = RUNS_DIR / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def recent(limit: int = 40) -> list[dict]:
    if not RUNS_DIR.is_dir():
        return []
    out: list[dict] = []
    # Filenames carry a UTC timestamp, so reverse-sorting them is newest-first
    # without opening anything.
    for path in sorted(RUNS_DIR.glob("r-*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "id": data.get("id", path.stem),
                "task": data.get("task", ""),
                "started": data.get("started", ""),
                "grounded": data.get("grounded", True),
                "verdict": data.get("verdict", "unknown"),
                "shipped": data.get("shipped", False),
                "attempts": len(data.get("attempts", [])),
                "wrote_back": data.get("writeback") is not None,
                "duration_ms": data.get("duration_ms", 0),
            }
        )
    return out


def tally(runs: list[dict]) -> dict:
    """Counts for the history header. Computed from the records themselves so
    it cannot drift from what is listed underneath it."""
    return {
        "total": len(runs),
        "shipped": sum(1 for r in runs if r.get("shipped")),
        "blocked": sum(
            1 for r in runs if not r.get("shipped") and r.get("verdict") == "fail"
        ),
        "escalated": sum(1 for r in runs if r.get("verdict") == "escalate"),
        "first_try": sum(
            1 for r in runs if r.get("shipped") and r.get("attempts", 0) == 1
        ),
    }
