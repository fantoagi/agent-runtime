from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceptance import AcceptanceSuiteError


class AcceptanceComparisonError(AcceptanceSuiteError):
    """A persisted acceptance report cannot be compared safely."""


@dataclass(frozen=True, slots=True)
class AcceptanceRegression:
    case_name: str | None
    attempt: int | None
    kind: str
    message: str
    baseline: Any = None
    candidate: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "attempt": self.attempt,
            "kind": self.kind,
            "message": self.message,
            "baseline": self.baseline,
            "candidate": self.candidate,
        }


@dataclass(frozen=True, slots=True)
class AcceptanceComparison:
    status: str
    baseline_path: str
    candidate_path: str
    baseline_report_id: str | None
    candidate_report_id: str | None
    suite_name: str | None
    regressions: tuple[AcceptanceRegression, ...]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "baseline_path": self.baseline_path,
            "candidate_path": self.candidate_path,
            "baseline_report_id": self.baseline_report_id,
            "candidate_report_id": self.candidate_report_id,
            "suite_name": self.suite_name,
            "regression_count": len(self.regressions),
            "warning_count": len(self.warnings),
            "regressions": [item.to_dict() for item in self.regressions],
            "warnings": list(self.warnings),
        }


def compare_acceptance_reports(
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> AcceptanceComparison:
    """Compare two redacted acceptance reports without contacting a model.

    A comparison is a gate only for durable regressions: a previously passing
    attempt that no longer passes, loss of post-change verification, newly
    observed protocol violations, or newly observed unknown tool outcomes.
    Runtime/provider/model changes and performance drift are reported as
    warnings so the same command remains useful across intentional upgrades.
    """
    baseline_file = Path(baseline_path).resolve()
    candidate_file = Path(candidate_path).resolve()
    baseline = _load_report(baseline_file)
    candidate = _load_report(candidate_file)
    regressions: list[AcceptanceRegression] = []
    warnings: list[str] = []

    identity_fields = ("suite_name", "suite_version", "suite_checksum")
    identity_mismatches = [
        field
        for field in identity_fields
        if baseline.get(field) != candidate.get(field)
    ]
    if identity_mismatches:
        regressions.append(
            AcceptanceRegression(
                None,
                None,
                "incompatible_suite",
                "Baseline and candidate use different suite identity fields: "
                + ", ".join(identity_mismatches),
                {field: baseline.get(field) for field in identity_fields},
                {field: candidate.get(field) for field in identity_fields},
            )
        )

    for field in ("provider", "model", "runtime_version"):
        if baseline.get(field) != candidate.get(field):
            warnings.append(
                f"{field} changed from {baseline.get(field)!r} to {candidate.get(field)!r}."
            )

    baseline_results = _index_results(baseline, "baseline")
    candidate_results = _index_results(candidate, "candidate")
    for key, baseline_result in sorted(baseline_results.items()):
        case_name, attempt = key
        candidate_result = candidate_results.get(key)
        if candidate_result is None:
            regressions.append(
                AcceptanceRegression(
                    case_name,
                    attempt,
                    "missing_attempt",
                    "Candidate report is missing an attempt present in the baseline.",
                    _result_summary(baseline_result),
                    None,
                )
            )
            continue
        if bool(baseline_result.get("passed")) and not bool(candidate_result.get("passed")):
            regressions.append(
                AcceptanceRegression(
                    case_name,
                    attempt,
                    "case_failed",
                    "An attempt that passed in the baseline failed in the candidate.",
                    _result_summary(baseline_result),
                    _result_summary(candidate_result),
                )
            )
            continue
        baseline_metrics = _mapping(baseline_result.get("metrics"))
        candidate_metrics = _mapping(candidate_result.get("metrics"))
        if (
            baseline_metrics.get("verification_status") == "verified"
            and candidate_metrics.get("verification_status") != "verified"
        ):
            regressions.append(
                AcceptanceRegression(
                    case_name,
                    attempt,
                    "verification_regressed",
                    "Post-change verification regressed from verified to a non-verified state.",
                    baseline_metrics.get("verification_status"),
                    candidate_metrics.get("verification_status"),
                )
            )
        if (
            int(baseline_metrics.get("protocol_violations", 0)) == 0
            and int(candidate_metrics.get("protocol_violations", 0)) > 0
        ):
            regressions.append(
                AcceptanceRegression(
                    case_name,
                    attempt,
                    "protocol_regressed",
                    "Candidate introduced protocol violations where the baseline had none.",
                    baseline_metrics.get("protocol_violations", 0),
                    candidate_metrics.get("protocol_violations", 0),
                )
            )
        if (
            int(baseline_metrics.get("unknown_tool_calls", 0)) == 0
            and int(candidate_metrics.get("unknown_tool_calls", 0)) > 0
        ):
            regressions.append(
                AcceptanceRegression(
                    case_name,
                    attempt,
                    "unknown_outcome_regressed",
                    "Candidate introduced unknown tool outcomes where the baseline had none.",
                    baseline_metrics.get("unknown_tool_calls", 0),
                    candidate_metrics.get("unknown_tool_calls", 0),
                )
            )
        _append_performance_warnings(warnings, case_name, attempt, baseline_result, candidate_result)

    extra_keys = sorted(set(candidate_results) - set(baseline_results))
    if extra_keys:
        warnings.append(
            "Candidate contains additional attempts not present in baseline: "
            + ", ".join(f"{case}#{attempt}" for case, attempt in extra_keys)
            + "."
        )

    status = "incompatible" if any(item.kind == "incompatible_suite" for item in regressions) else (
        "failed" if regressions else "passed"
    )
    return AcceptanceComparison(
        status=status,
        baseline_path=str(baseline_file),
        candidate_path=str(candidate_file),
        baseline_report_id=_optional_string(baseline.get("id")),
        candidate_report_id=_optional_string(candidate.get("id")),
        suite_name=_optional_string(candidate.get("suite_name")),
        regressions=tuple(regressions),
        warnings=tuple(warnings),
    )


def _load_report(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AcceptanceComparisonError(f"Acceptance report was not found: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceComparisonError(f"Acceptance report is unreadable: {path}") from error
    if not isinstance(payload, dict):
        raise AcceptanceComparisonError(f"Acceptance report root must be an object: {path}")
    if not isinstance(payload.get("results"), list):
        raise AcceptanceComparisonError(f"Acceptance report results must be a list: {path}")
    return payload


def _index_results(report: Mapping[str, Any], label: str) -> dict[tuple[str, int], Mapping[str, Any]]:
    indexed: dict[tuple[str, int], Mapping[str, Any]] = {}
    for raw_result in report["results"]:
        if not isinstance(raw_result, dict):
            raise AcceptanceComparisonError(f"{label} report contains a non-object result.")
        case_name = raw_result.get("case_name")
        attempt = raw_result.get("attempt")
        if not isinstance(case_name, str) or not case_name:
            raise AcceptanceComparisonError(f"{label} report contains an invalid case_name.")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise AcceptanceComparisonError(f"{label} report contains an invalid attempt for {case_name}.")
        key = (case_name, attempt)
        if key in indexed:
            raise AcceptanceComparisonError(
                f"{label} report contains duplicate result {case_name}#{attempt}."
            )
        indexed[key] = raw_result
    return indexed


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _result_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _mapping(result.get("metrics"))
    return {
        "passed": result.get("passed"),
        "status": result.get("status"),
        "error_code": result.get("error_code"),
        "verification_status": metrics.get("verification_status"),
        "protocol_violations": metrics.get("protocol_violations", 0),
        "unknown_tool_calls": metrics.get("unknown_tool_calls", 0),
    }


def _append_performance_warnings(
    warnings: list[str],
    case_name: str,
    attempt: int,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    baseline_duration = float(baseline.get("duration_ms", 0) or 0)
    candidate_duration = float(candidate.get("duration_ms", 0) or 0)
    if baseline_duration > 0 and candidate_duration > baseline_duration * 1.2:
        warnings.append(
            f"{case_name}#{attempt} duration increased by more than 20% "
            f"({baseline_duration:.1f}ms -> {candidate_duration:.1f}ms)."
        )

