"""Progress events, so a caller can watch the loop instead of waiting for it.

The pipeline reports what it is doing through a plain callable. The CLI ignores
it, the console streams it to a browser, and tests can assert on the sequence
without a server.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Event:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"data: {json.dumps({'kind': self.kind, **self.data})}\n\n"


def gate_event(attempt: int, result) -> Event:
    return Event(
        "gate",
        {
            "attempt": attempt,
            "gate": result.gate,
            "verdict": result.verdict.value,
            "findings": [asdict(f) for f in result.findings],
        },
    )


class NullReporter:
    """Used when nobody is watching."""

    def __call__(self, event: Event) -> None:  # pragma: no cover - trivial
        pass
