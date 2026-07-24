"""Verification gates: the contract every check implements.

A gate inspects a generated artifact and returns a verdict. Artifacts only ship
when every gate passes. A failing gate must say precisely what is wrong and, when
it can, how to fix it -- the repair loop feeds that text back to the generator.

Gates never guess. When a gate cannot decide (metadata missing, ambiguous match),
it returns ESCALATE rather than a pass, and the run stops for a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ESCALATE = "escalate"


@dataclass
class Finding:
    """One specific problem found in an artifact."""

    message: str
    # Where in the artifact the problem is, when known: e.g. "column: customer_region"
    locus: str | None = None
    # Concrete repair instruction handed back to the generator.
    suggestion: str | None = None
    # What this claim is based on, so a human can check it. Grounding is the point.
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [self.message]
        if self.locus:
            parts.append(f"at {self.locus}")
        if self.suggestion:
            parts.append(f"fix: {self.suggestion}")
        if self.evidence:
            parts.append("evidence: " + "; ".join(self.evidence))
        return " | ".join(parts)


@dataclass
class GateResult:
    gate: str
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    def render(self) -> str:
        head = f"[{self.verdict.value.upper()}] {self.gate}"
        if not self.findings:
            return head
        return head + "\n" + "\n".join(f"  - {f.render()}" for f in self.findings)


class Gate:
    """Base class. Subclasses implement check()."""

    name: str = "gate"

    def check(self, artifact, context) -> GateResult:  # pragma: no cover - interface
        raise NotImplementedError

    def _pass(self) -> GateResult:
        return GateResult(gate=self.name, verdict=Verdict.PASS)

    def _fail(self, findings: list[Finding]) -> GateResult:
        return GateResult(gate=self.name, verdict=Verdict.FAIL, findings=findings)

    def _escalate(self, findings: list[Finding]) -> GateResult:
        return GateResult(gate=self.name, verdict=Verdict.ESCALATE, findings=findings)
