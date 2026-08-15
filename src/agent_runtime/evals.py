from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .domain import AgentDefinition, AgentRun, RunStatus, new_id, utc_now
from .runtime import Runtime


@dataclass(slots=True)
class EvalCase:
    name: str
    input: str
    expected_output: str | None = None
    expected_contains: list[str] = field(default_factory=list)
    expected_status: str = "completed"
    metadata: dict[str, Any] = field(default_factory=dict)
    expected_child_count: int | None = None
    expected_memory_count: int | None = None


@dataclass(slots=True)
class EvalSuite:
    name: str
    cases: list[EvalCase]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvalAssertion:
    evaluator: str
    passed: bool
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator": self.evaluator,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass(slots=True)
class EvalCaseResult:
    case_name: str
    run_id: str
    trace_id: str
    status: str
    output: str | None
    passed: bool
    duration_ms: float
    assertions: list[EvalAssertion]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "status": self.status,
            "output": self.output,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "assertions": [assertion.to_dict() for assertion in self.assertions],
        }


@dataclass(slots=True)
class EvalReport:
    id: str
    suite_name: str
    started_at: datetime
    completed_at: datetime
    results: list[EvalCaseResult]
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_path: str | None = None

    @property
    def total_cases(self) -> int:
        return len(self.results)

    @property
    def passed_cases(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def failed_cases(self) -> int:
        return self.total_cases - self.passed_cases

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return round(self.passed_cases / self.total_cases, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "suite_name": self.suite_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "pass_rate": self.pass_rate,
            "metadata": self.metadata,
            "artifact_path": self.artifact_path,
            "results": [result.to_dict() for result in self.results],
        }


class Evaluator(Protocol):
    name: str

    def supports(self, case: EvalCase) -> bool: ...

    def evaluate(self, case: EvalCase, run: AgentRun) -> EvalAssertion: ...


class ExpectedStatusEvaluator:
    name = "expected_status"

    def supports(self, case: EvalCase) -> bool:
        return bool(case.expected_status)

    def evaluate(self, case: EvalCase, run: AgentRun) -> EvalAssertion:
        actual = run.status.value
        passed = actual == case.expected_status
        return EvalAssertion(
            evaluator=self.name,
            passed=passed,
            expected=case.expected_status,
            actual=actual,
            message="Run status matched." if passed else "Run status did not match.",
        )


class ExactMatchEvaluator:
    name = "exact_match"

    def __init__(self, *, strip: bool = True, case_sensitive: bool = True) -> None:
        self.strip = strip
        self.case_sensitive = case_sensitive

    def supports(self, case: EvalCase) -> bool:
        return case.expected_output is not None

    def evaluate(self, case: EvalCase, run: AgentRun) -> EvalAssertion:
        expected = case.expected_output or ""
        actual = run.result or ""
        normalized_expected = self._normalize(expected)
        normalized_actual = self._normalize(actual)
        passed = normalized_actual == normalized_expected
        return EvalAssertion(
            evaluator=self.name,
            passed=passed,
            expected=expected,
            actual=actual,
            message="Output matched exactly." if passed else "Output did not match exactly.",
        )

    def _normalize(self, value: str) -> str:
        if self.strip:
            value = value.strip()
        if not self.case_sensitive:
            value = value.casefold()
        return value


class ContainsEvaluator:
    name = "contains"

    def __init__(self, *, case_sensitive: bool = True) -> None:
        self.case_sensitive = case_sensitive

    def supports(self, case: EvalCase) -> bool:
        return bool(case.expected_contains)

    def evaluate(self, case: EvalCase, run: AgentRun) -> EvalAssertion:
        actual = run.result or ""
        candidate = actual if self.case_sensitive else actual.casefold()
        expected = case.expected_contains
        missing = [
            item
            for item in expected
            if (item if self.case_sensitive else item.casefold()) not in candidate
        ]
        passed = not missing
        return EvalAssertion(
            evaluator=self.name,
            passed=passed,
            expected=expected,
            actual=actual,
            message="All expected fragments were present."
            if passed
            else f"Missing fragments: {missing}",
        )


class EvalRunner:
    """Run deterministic eval suites through the same durable Runtime path."""

    def __init__(
        self,
        runtime: Runtime,
        evaluators: list[Evaluator] | None = None,
        *,
        persist_report: bool = True,
    ) -> None:
        self.runtime = runtime
        self.evaluators = evaluators or [
            ExpectedStatusEvaluator(),
            ExactMatchEvaluator(),
            ContainsEvaluator(),
        ]
        self.persist_report = persist_report

    async def run(
        self,
        suite: EvalSuite,
        agent: AgentDefinition | str,
    ) -> EvalReport:
        report_id = new_id("eval")
        started_at = utc_now()
        results: list[EvalCaseResult] = []
        for case in suite.cases:
            case_started = utc_now()
            run = await self.runtime.run(
                agent,
                case.input,
                {
                    **case.metadata,
                    "eval_report_id": report_id,
                    "eval_suite": suite.name,
                    "eval_case": case.name,
                },
            )
            assertions = [
                evaluator.evaluate(case, run)
                for evaluator in self.evaluators
                if evaluator.supports(case)
            ]
            results.append(
                EvalCaseResult(
                    case_name=case.name,
                    run_id=run.id,
                    trace_id=str(run.metadata.get("trace_id") or run.id),
                    status=run.status.value,
                    output=run.result,
                    passed=all(assertion.passed for assertion in assertions),
                    duration_ms=round(
                        max(0.0, (utc_now() - case_started).total_seconds() * 1000),
                        3,
                    ),
                    assertions=assertions,
                )
            )
        report = EvalReport(
            id=report_id,
            suite_name=suite.name,
            started_at=started_at,
            completed_at=utc_now(),
            results=results,
            metadata=suite.metadata,
        )
        if self.persist_report:
            path = self.runtime.artifacts.write_text(
                report.id,
                "eval-report.json",
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            )
            report.artifact_path = str(path)
            path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return report

class WorkflowEvalRunner:
    """Evaluate a sequential or parallel workflow through its durable parent Run."""

    def __init__(
        self,
        runtime: Runtime,
        evaluators: list[Evaluator] | None = None,
        *,
        persist_report: bool = True,
    ) -> None:
        self.runtime = runtime
        self.evaluators = evaluators or [
            ExpectedStatusEvaluator(),
            ExactMatchEvaluator(),
            ContainsEvaluator(),
        ]
        self.persist_report = persist_report

    async def run(self, suite: EvalSuite, workflow: Any) -> EvalReport:
        report_id = new_id("workflow_eval")
        started_at = utc_now()
        results: list[EvalCaseResult] = []
        for case in suite.cases:
            case_started = utc_now()
            execution = await workflow.run(
                self.runtime,
                case.input,
                metadata={
                    **case.metadata,
                    "eval_report_id": report_id,
                    "eval_suite": suite.name,
                    "eval_case": case.name,
                    "eval_kind": "workflow",
                },
            )
            run = execution.parent
            assertions = [
                evaluator.evaluate(case, run)
                for evaluator in self.evaluators
                if evaluator.supports(case)
            ]
            if case.expected_child_count is not None:
                actual_count = len(execution.children)
                assertions.append(
                    EvalAssertion(
                        evaluator="expected_child_count",
                        passed=actual_count == case.expected_child_count,
                        expected=case.expected_child_count,
                        actual=actual_count,
                        message=(
                            "Child Run count matched."
                            if actual_count == case.expected_child_count
                            else "Child Run count did not match."
                        ),
                    )
                )
            results.append(
                EvalCaseResult(
                    case_name=case.name,
                    run_id=run.id,
                    trace_id=str(run.metadata.get("trace_id") or run.id),
                    status=run.status.value,
                    output=run.result,
                    passed=all(assertion.passed for assertion in assertions),
                    duration_ms=round(
                        max(0.0, (utc_now() - case_started).total_seconds() * 1000), 3
                    ),
                    assertions=assertions,
                )
            )
        report = EvalReport(
            id=report_id,
            suite_name=suite.name,
            started_at=started_at,
            completed_at=utc_now(),
            results=results,
            metadata={**suite.metadata, "eval_kind": "workflow"},
        )
        if self.persist_report:
            path = self.runtime.artifacts.write_text(
                report.id,
                "workflow-eval-report.json",
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            )
            report.artifact_path = str(path)
            path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return report

class MemoryEvalRunner:
    """Evaluate scoped memory retrieval with the regular Eval report format."""

    def __init__(
        self,
        runtime: Runtime,
        evaluators: list[Evaluator] | None = None,
        *,
        persist_report: bool = True,
    ) -> None:
        self.runtime = runtime
        self.evaluators = evaluators or [
            ExpectedStatusEvaluator(),
            ExactMatchEvaluator(),
            ContainsEvaluator(),
        ]
        self.persist_report = persist_report

    async def run(
        self,
        suite: EvalSuite,
        *,
        session_id: str | None = None,
        agent_name: str | None = None,
    ) -> EvalReport:
        report_id = new_id("eval")
        started_at = utc_now()
        results: list[EvalCaseResult] = []
        for case in suite.cases:
            case_started = utc_now()
            matches = self.runtime.search_memory(
                case.input,
                session_id=session_id,
                agent_name=agent_name,
            )
            run = AgentRun.create(
                "memory-search",
                case.input,
                {
                    "trace_id": new_id("trace"),
                    "eval_report_id": report_id,
                    "eval_kind": "memory",
                },
            )
            run.transition_to(RunStatus.RUNNING)
            run.result = "\n".join(match.record.content for match in matches)
            run.transition_to(RunStatus.COMPLETED)
            assertions = [
                evaluator.evaluate(case, run)
                for evaluator in self.evaluators
                if evaluator.supports(case)
            ]
            if case.expected_memory_count is not None:
                actual_count = len(matches)
                assertions.append(
                    EvalAssertion(
                        evaluator="expected_memory_count",
                        passed=actual_count == case.expected_memory_count,
                        expected=case.expected_memory_count,
                        actual=actual_count,
                        message=(
                            "Memory result count matched."
                            if actual_count == case.expected_memory_count
                            else "Memory result count did not match."
                        ),
                    )
                )
            results.append(
                EvalCaseResult(
                    case_name=case.name,
                    run_id=run.id,
                    trace_id=str(run.metadata["trace_id"]),
                    status=run.status.value,
                    output=run.result,
                    passed=all(assertion.passed for assertion in assertions),
                    duration_ms=round(
                        max(0.0, (utc_now() - case_started).total_seconds() * 1000), 3
                    ),
                    assertions=assertions,
                )
            )
        report = EvalReport(
            id=report_id,
            suite_name=suite.name,
            started_at=started_at,
            completed_at=utc_now(),
            results=results,
            metadata={**suite.metadata, "eval_kind": "memory"},
        )
        if self.persist_report:
            path = self.runtime.artifacts.write_text(
                report.id,
                "memory-eval-report.json",
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            )
            report.artifact_path = str(path)
            path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return report
