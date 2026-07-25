"""Ground, generate, verify, repair.

The loop that makes generated code trustworthy: every candidate is analyzed and
run past every gate before anyone sees it. Failures become repair instructions
and the generator gets one more try; escalations stop the run and go to a human,
because the fix is missing metadata, not worse SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .artifact import Artifact
from .gates.base import GateResult, Verdict
from .sql_analysis import analyze


@dataclass
class Attempt:
    artifact: Artifact
    results: list[GateResult] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if any(r.verdict is Verdict.ESCALATE for r in self.results):
            return Verdict.ESCALATE
        if any(r.verdict is Verdict.FAIL for r in self.results):
            return Verdict.FAIL
        return Verdict.PASS

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]

    def repair_brief(self) -> str:
        """What to tell the generator so its next attempt is better."""
        lines = ["The previous SQL was rejected. Fix exactly these problems:"]
        for result in self.failures:
            for finding in result.findings:
                lines.append(f"- [{result.gate}] {finding.render()}")
        return "\n".join(lines)


@dataclass
class RunReport:
    """The evidence a reviewer needs to trust (or reject) the artifact."""

    task: str
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def final(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def verdict(self) -> Verdict:
        return self.final.verdict if self.final else Verdict.FAIL

    @property
    def shipped(self) -> bool:
        return self.verdict is Verdict.PASS

    def render(self) -> str:
        lines = [f"# Verification report: {self.task}", ""]
        for i, attempt in enumerate(self.attempts, 1):
            lines.append(f"## Attempt {i}: {attempt.verdict.value.upper()}")
            for result in attempt.results:
                lines.append(result.render())
            lines.append("")
        outcome = "SHIPPED" if self.shipped else self.verdict.value.upper()
        lines.append(f"**Outcome: {outcome}** after {len(self.attempts)} attempt(s)")
        return "\n".join(lines)


class VerifiedGenerator:
    """Runs the generate-verify-repair loop.

    generator(task, brief) -> Artifact. brief is None on the first attempt and
    carries the previous failures on retries.
    """

    def __init__(self, generator, gates, context, max_attempts: int = 3):
        self.generator = generator
        self.gates = gates
        self.context = context
        self.max_attempts = max_attempts

    def run(self, task: str, name_to_urn: dict[str, str]) -> RunReport:
        report = RunReport(task=task)
        brief: str | None = None

        for _ in range(self.max_attempts):
            artifact = self.generator(task, brief)
            analysis = analyze(artifact.sql, name_to_urn)
            artifact.column_refs = analysis.column_refs
            artifact.source_tables = {t.name: t.urn for t in analysis.tables if t.urn}

            attempt = Attempt(artifact=artifact)
            attempt.results.extend(_analysis_gate(analysis))
            for gate in self.gates:
                attempt.results.append(gate.check(artifact, self.context))
            report.attempts.append(attempt)

            if attempt.verdict is Verdict.PASS:
                break
            if attempt.verdict is Verdict.ESCALATE:
                # More attempts won't conjure the missing metadata.
                break
            brief = attempt.repair_brief()

        return report


def _analysis_gate(analysis) -> list[GateResult]:
    """Turn parse problems into gate results so they flow through one channel."""
    from .gates.base import Finding

    findings_fail: list[Finding] = []
    findings_escalate: list[Finding] = []

    if analysis.parse_error:
        findings_fail.append(
            Finding(message="SQL could not be parsed", suggestion=analysis.parse_error)
        )
    for name in analysis.unresolved_tables:
        findings_escalate.append(
            Finding(
                message="Table is not in the catalog, so its columns cannot be verified",
                locus=name,
                suggestion="Ingest it into DataHub, or use a catalogued table",
            )
        )
    for col in analysis.ambiguous:
        findings_fail.append(
            Finding(
                message="Unqualified column in a multi-table query is ambiguous",
                locus=col,
                suggestion=f"qualify it, e.g. `orders.{col}`",
            )
        )

    results: list[GateResult] = []
    if findings_escalate:
        results.append(
            GateResult(
                gate="sql_analysis",
                verdict=Verdict.ESCALATE,
                findings=findings_escalate + findings_fail,
            )
        )
    elif findings_fail:
        results.append(
            GateResult(
                gate="sql_analysis", verdict=Verdict.FAIL, findings=findings_fail
            )
        )
    else:
        results.append(GateResult(gate="sql_analysis", verdict=Verdict.PASS))
    return results
