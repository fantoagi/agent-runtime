from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime.acceptance_compare import (
    AcceptanceComparisonError,
    compare_acceptance_reports,
)


def report_payload(*, passed: bool = True, verification_status: str = "not_required", protocol_violations: int = 0, unknown_tool_calls: int = 0, duration_ms: float = 100.0, model: str = "model") -> dict[str, object]:
    return {
        "id": "report-1",
        "suite_name": "local-real-model",
        "suite_version": 1,
        "suite_checksum": "suite-checksum",
        "runtime_version": "0.8.22",
        "provider": "openai-compatible",
        "model": model,
        "results": [
            {
                "case_name": "case-a",
                "attempt": 1,
                "status": "completed" if passed else "failed",
                "passed": passed,
                "duration_ms": duration_ms,
                "error_code": None if passed else "run_failed",
                "metrics": {
                    "verification_status": verification_status,
                    "protocol_violations": protocol_violations,
                    "unknown_tool_calls": unknown_tool_calls,
                },
            }
        ],
    }


def write_report(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_compare_acceptance_reports_passes_and_reports_non_gate_warnings(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    write_report(baseline, report_payload(model="old-model"))
    write_report(candidate, report_payload(model="new-model", duration_ms=130.0))

    comparison = compare_acceptance_reports(baseline, candidate)

    assert comparison.status == "passed"
    assert comparison.regressions == ()
    assert any("model changed" in warning for warning in comparison.warnings)
    assert any("duration increased" in warning for warning in comparison.warnings)


def test_compare_acceptance_reports_detects_failure_and_evidence_regression(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    write_report(baseline, report_payload(verification_status="verified"))
    write_report(
        candidate,
        report_payload(
            passed=False,
            verification_status="unverified",
            protocol_violations=1,
            unknown_tool_calls=1,
        ),
    )

    comparison = compare_acceptance_reports(baseline, candidate)

    assert comparison.status == "failed"
    assert {item.kind for item in comparison.regressions} == {"case_failed"}


def test_compare_acceptance_reports_detects_metric_regressions_when_both_attempts_fail(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    write_report(baseline, report_payload(passed=False, verification_status="verified"))
    write_report(
        candidate,
        report_payload(
            passed=False,
            verification_status="unverified",
            protocol_violations=1,
            unknown_tool_calls=1,
        ),
    )

    comparison = compare_acceptance_reports(baseline, candidate)

    assert comparison.status == "failed"
    assert {item.kind for item in comparison.regressions} == {
        "verification_regressed",
        "protocol_regressed",
        "unknown_outcome_regressed",
    }


def test_compare_acceptance_reports_rejects_different_suite(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    write_report(baseline, report_payload())
    candidate_payload = report_payload()
    candidate_payload["suite_checksum"] = "different"
    write_report(candidate, candidate_payload)

    comparison = compare_acceptance_reports(baseline, candidate)

    assert comparison.status == "incompatible"
    assert comparison.regressions[0].kind == "incompatible_suite"


def test_compare_acceptance_reports_rejects_duplicate_results(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    payload = report_payload()
    payload["results"] = [payload["results"][0], payload["results"][0]]  # type: ignore[index]
    write_report(baseline, payload)
    write_report(candidate, report_payload())

    with pytest.raises(AcceptanceComparisonError, match="duplicate result"):
        compare_acceptance_reports(baseline, candidate)
