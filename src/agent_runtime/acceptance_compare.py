from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceptance import AcceptanceManifest, AcceptanceSuiteError


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
    scope: str
    compared_case_names: tuple[str, ...]
    baseline_path: str
    candidate_path: str
    baseline_report_id: str | None
    candidate_report_id: str | None
    suite_name: str | None
    regressions: tuple[AcceptanceRegression, ...]
    warnings: tuple[str, ...]
    manifest_differences: Mapping[str, Mapping[str, Any]]

    @property
    def passed(self) -> bool:
        return self.status in {"passed", "partial"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "scope": self.scope,
            "compared_case_names": list(self.compared_case_names),
            "baseline_path": self.baseline_path,
            "candidate_path": self.candidate_path,
            "baseline_report_id": self.baseline_report_id,
            "candidate_report_id": self.candidate_report_id,
            "suite_name": self.suite_name,
            "regression_count": len(self.regressions),
            "warning_count": len(self.warnings),
            "regressions": [item.to_dict() for item in self.regressions],
            "warnings": list(self.warnings),
            "manifest_differences": {
                key: dict(value) for key, value in self.manifest_differences.items()
            },
        }


def compare_acceptance_reports(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    case_names: Sequence[str] = (),
) -> AcceptanceComparison:
    """Compare two redacted acceptance reports without contacting a model.

    Strict comparisons require identical Case/Attempt keys. A caller may pass
    ``case_names`` to explicitly compare a partial scope; that result is marked
    ``partial`` and still requires every requested Case/Attempt to exist in
    both reports.
    """
    baseline_file = Path(baseline_path).resolve()
    candidate_file = Path(candidate_path).resolve()
    baseline = _load_report(baseline_file)
    candidate = _load_report(candidate_file)
    baseline_results = _index_results(baseline, "baseline")
    candidate_results = _index_results(candidate, "candidate")
    baseline_scope, baseline_scope_error = _report_scope(baseline, baseline_results)
    candidate_scope, candidate_scope_error = _report_scope(candidate, candidate_results)
    baseline_manifest = AcceptanceManifest.from_report_payload(baseline)
    candidate_manifest = AcceptanceManifest.from_report_payload(candidate)
    manifest_differences = _manifest_differences(baseline_manifest, candidate_manifest)
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

    if baseline_scope_error:
        regressions.append(
            AcceptanceRegression(
                None,
                None,
                "incomplete_scope",
                f"Baseline report scope is invalid: {baseline_scope_error}",
                baseline_scope,
                None,
            )
        )
    if candidate_scope_error:
        regressions.append(
            AcceptanceRegression(
                None,
                None,
                "incomplete_scope",
                f"Candidate report scope is invalid: {candidate_scope_error}",
                None,
                candidate_scope,
            )
        )

    for field in ("provider", "model", "runtime_version"):
        if baseline.get(field) != candidate.get(field):
            warnings.append(
                f"{field} changed from {baseline.get(field)!r} to {candidate.get(field)!r}."
            )
    if "selection" not in baseline:
        warnings.append("Baseline report has no explicit selection metadata; scope was inferred.")
    if "selection" not in candidate:
        warnings.append("Candidate report has no explicit selection metadata; scope was inferred.")

    requested = _normalize_case_names(case_names)
    if case_names and not requested:
        raise AcceptanceComparisonError("At least one non-empty case name is required for partial comparison.")

    if requested:
        compared_case_names = requested
        baseline_keys = _keys_for_cases(baseline_results, requested)
        candidate_keys = _keys_for_cases(candidate_results, requested)
        baseline_case_names = set(baseline_scope.get("case_names", ()))
        candidate_case_names = set(candidate_scope.get("case_names", ()))
        missing_baseline = [name for name in requested if name not in baseline_case_names]
        missing_candidate = [name for name in requested if name not in candidate_case_names]
        if missing_baseline or missing_candidate:
            regressions.append(
                AcceptanceRegression(
                    None,
                    None,
                    "scope_mismatch",
                    "Requested partial comparison Case is missing from one or both reports.",
                    {"missing": missing_baseline},
                    {"missing": missing_candidate},
                )
            )
        elif baseline_keys != candidate_keys:
            regressions.append(
                AcceptanceRegression(
                    None,
                    None,
                    "scope_mismatch",
                    "Requested partial comparison has different Case/Attempt keys.",
                    _sorted_key_strings(baseline_keys),
                    _sorted_key_strings(candidate_keys),
                )
            )
        comparison_baseline = {key: baseline_results[key] for key in baseline_keys & candidate_keys}
        comparison_candidate = {key: candidate_results[key] for key in baseline_keys & candidate_keys}
        scope = "partial"
        warnings.append("Comparison explicitly limited to: " + ", ".join(requested) + ".")
    else:
        compared_case_names = tuple(baseline_scope["case_names"])
        if set(baseline_results) != set(candidate_results):
            regressions.append(
                AcceptanceRegression(
                    None,
                    None,
                    "scope_mismatch",
                    "Baseline and candidate must contain the same Case/Attempt keys for a strict comparison.",
                    _scope_comparison_payload(baseline_scope, baseline_results),
                    _scope_comparison_payload(candidate_scope, candidate_results),
                )
            )
            comparison_baseline = {}
            comparison_candidate = {}
        else:
            comparison_baseline = baseline_results
            comparison_candidate = candidate_results
        scope = "full"

    if not any(item.kind in {"incompatible_suite", "incomplete_scope", "scope_mismatch"} for item in regressions):
        for key in sorted(comparison_baseline):
            baseline_result = comparison_baseline[key]
            candidate_result = comparison_candidate[key]
            case_name, attempt = key
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

    incompatible = any(
        item.kind in {"incompatible_suite", "incomplete_scope", "scope_mismatch"}
        for item in regressions
    )
    status = "incompatible" if incompatible else ("failed" if regressions else ("partial" if requested else "passed"))
    return AcceptanceComparison(
        status=status,
        scope=scope,
        compared_case_names=tuple(compared_case_names),
        baseline_path=str(baseline_file),
        candidate_path=str(candidate_file),
        baseline_report_id=_optional_string(baseline.get("id")),
        candidate_report_id=_optional_string(candidate.get("id")),
        suite_name=_optional_string(candidate.get("suite_name")),
        regressions=tuple(regressions),
        warnings=tuple(warnings),
        manifest_differences=manifest_differences,
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


def _report_scope(
    report: Mapping[str, Any],
    results: Mapping[tuple[str, int], Mapping[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    raw_selection = report.get("selection")
    if raw_selection is None:
        case_names = tuple(sorted({case for case, _ in results}))
        attempts = [attempt for _, attempt in results]
        repeat = max(attempts) if attempts else None
        return (
            {
                "case_names": list(case_names),
                "repeat": repeat,
                "expected_attempts": None,
                "actual_attempts": len(results),
                "explicit": False,
            },
            None,
        )
    if not isinstance(raw_selection, dict):
        return ({"explicit": True}, "selection must be an object")
    raw_cases = raw_selection.get("case_names")
    repeat = raw_selection.get("repeat")
    expected = raw_selection.get("expected_attempts")
    actual = raw_selection.get("actual_attempts")
    if (
        not isinstance(raw_cases, list)
        or not raw_cases
        or any(not isinstance(item, str) or not item for item in raw_cases)
        or len(raw_cases) != len(set(raw_cases))
    ):
        return ({"explicit": True}, "selection.case_names must be a unique non-empty string list")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        return ({"explicit": True, "case_names": raw_cases}, "selection.repeat must be a positive integer")
    calculated = len(raw_cases) * repeat
    if expected != calculated:
        return (
            {"explicit": True, "case_names": raw_cases, "repeat": repeat, "expected_attempts": expected},
            "selection.expected_attempts does not match case_names × repeat",
        )
    if isinstance(actual, bool) or not isinstance(actual, int) or actual < 0:
        return (
            {
                "explicit": True,
                "case_names": raw_cases,
                "repeat": repeat,
                "expected_attempts": calculated,
                "actual_attempts": actual,
            },
            "selection.actual_attempts must be a non-negative integer",
        )
    if actual != len(results):
        return (
            {
                "explicit": True,
                "case_names": raw_cases,
                "repeat": repeat,
                "expected_attempts": calculated,
                "actual_attempts": actual,
            },
            "selection.actual_attempts does not match the result count",
        )
    expected_keys = {(case_name, attempt) for case_name in raw_cases for attempt in range(1, repeat + 1)}
    actual_keys = set(results)
    if expected_keys != actual_keys:
        return (
            {
                "explicit": True,
                "case_names": raw_cases,
                "repeat": repeat,
                "expected_attempts": calculated,
                "actual_attempts": len(results),
            },
            "selection does not cover exactly case_names × attempts 1..repeat",
        )
    return (
        {
            "explicit": True,
            "case_names": raw_cases,
            "repeat": repeat,
            "expected_attempts": calculated,
            "actual_attempts": len(results),
        },
        None,
    )


def _normalize_case_names(case_names: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in case_names if isinstance(name, str) and name))


def _keys_for_cases(results: Mapping[tuple[str, int], Mapping[str, Any]], case_names: Sequence[str]) -> set[tuple[str, int]]:
    requested = set(case_names)
    return {key for key in results if key[0] in requested}


def _sorted_key_strings(keys: set[tuple[str, int]]) -> list[str]:
    return [f"{case}#{attempt}" for case, attempt in sorted(keys)]


def _scope_comparison_payload(
    scope: Mapping[str, Any],
    results: Mapping[tuple[str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    payload = dict(scope)
    payload["keys"] = _sorted_key_strings(set(results))
    return payload


def _manifest_differences(
    baseline: AcceptanceManifest,
    candidate: AcceptanceManifest,
) -> dict[str, Mapping[str, Any]]:
    differences: dict[str, Mapping[str, Any]] = {}
    for field in (
        "runtime_version",
        "git_commit",
        "python_version",
        "platform",
        "provider",
        "model",
        "suite",
        "cases",
        "repeat",
    ):
        baseline_value = getattr(baseline, field)
        candidate_value = getattr(candidate, field)
        if baseline_value == candidate_value:
            continue
        if field == "cases":
            baseline_value = list(baseline_value)
            candidate_value = list(candidate_value)
        differences[field] = {
            "baseline": baseline_value,
            "candidate": candidate_value,
        }
    return differences


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
