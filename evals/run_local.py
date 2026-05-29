from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from llm_reviewer import review_diff  # noqa: E402
from schemas import ReviewResult  # noqa: E402

DEFAULT_DATASET = Path(__file__).resolve().parent / "data" / "golden_cases.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "evals" / "results.json"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Load JSONL eval cases from disk."""
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            case = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on {path}:{line_number}: {exc}") from exc
        _validate_case(case, path, line_number)
        cases.append(case)

    if not cases:
        raise ValueError(f"No eval cases found in {path}")
    return cases


def _validate_case(case: dict[str, Any], path: Path, line_number: int) -> None:
    required_keys = {"id", "changed_files", "diff", "expected"}
    missing = sorted(required_keys - set(case))
    if missing:
        raise ValueError(f"Eval case in {path}:{line_number} is missing keys: {', '.join(missing)}")
    if not isinstance(case["changed_files"], list):
        raise ValueError(f"Eval case {case.get('id', '<unknown>')} has non-list changed_files")
    if not isinstance(case["expected"], dict):
        raise ValueError(f"Eval case {case.get('id', '<unknown>')} has non-object expected")


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    review = predict(case)
    checks = _run_checks(review, case["expected"])
    passed = all(check.passed for check in checks)
    return {
        "id": case["id"],
        "title": case.get("title", case["id"]),
        "tags": case.get("tags", []),
        "passed": passed,
        "checks": [check.to_dict() for check in checks],
        "actual": _review_to_report_dict(review),
    }


def predict(case: dict[str, Any]) -> ReviewResult:
    """Run the public review path in deterministic demo mode for one eval case."""
    previous_mode = os.environ.get("AI_REVIEW_MODE")
    os.environ["AI_REVIEW_MODE"] = "demo"
    try:
        changed_files = [str(path) for path in case["changed_files"]]
        return review_diff(str(case["diff"]), changed_files)
    finally:
        if previous_mode is None:
            os.environ.pop("AI_REVIEW_MODE", None)
        else:
            os.environ["AI_REVIEW_MODE"] = previous_mode


def _run_checks(review: ReviewResult, expected: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []

    if "risk_level" in expected:
        checks.append(
            CheckResult(
                name="risk_level",
                passed=review.risk_level == expected["risk_level"],
                detail=f"expected {expected['risk_level']!r}, got {review.risk_level!r}",
            )
        )

    min_counts = expected.get("min_counts", {})
    for field, minimum in min_counts.items():
        actual_value = getattr(review, field, [])
        actual_count = len(actual_value) if isinstance(actual_value, list) else 0
        checks.append(
            CheckResult(
                name=f"min_count:{field}",
                passed=actual_count >= int(minimum),
                detail=f"expected at least {minimum}, got {actual_count}",
            )
        )

    exact_counts = expected.get("exact_counts", {})
    for field, exact in exact_counts.items():
        actual_value = getattr(review, field, [])
        actual_count = len(actual_value) if isinstance(actual_value, list) else 0
        checks.append(
            CheckResult(
                name=f"exact_count:{field}",
                passed=actual_count == int(exact),
                detail=f"expected exactly {exact}, got {actual_count}",
            )
        )

    required_keywords = expected.get("required_keywords", {})
    for field, keywords in required_keywords.items():
        haystack = _field_text(review, field)
        for keyword in keywords:
            found = str(keyword).lower() in haystack.lower()
            checks.append(
                CheckResult(
                    name=f"keyword:{field}:{keyword}",
                    passed=found,
                    detail=f"keyword {keyword!r} {'found' if found else 'not found'} in {field}",
                )
            )

    changed_file_keywords = expected.get("changed_file_keywords", [])
    joined_changed_files = "\n".join(review.changed_files).lower()
    for keyword in changed_file_keywords:
        found = str(keyword).lower() in joined_changed_files
        checks.append(
            CheckResult(
                name=f"changed_file:{keyword}",
                passed=found,
                detail=f"changed_files contain {keyword!r}: {found}",
            )
        )

    if not checks:
        checks.append(CheckResult(name="schema_valid", passed=True, detail="ReviewResult parsed successfully"))

    return checks


def _field_text(review: ReviewResult, field: str) -> str:
    value = getattr(review, field, "")
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def _review_to_report_dict(review: ReviewResult) -> dict[str, Any]:
    if hasattr(review, "model_dump"):
        data = review.model_dump()
    else:
        data = review.dict()
    data.pop("automated_test_results", None)
    return data


def build_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    case_results = [evaluate_case(case) for case in cases]
    passed = sum(1 for result in case_results if result["passed"])
    total = len(case_results)
    failed = total - passed
    check_total = sum(len(result["checks"]) for result in case_results)
    check_passed = sum(
        1
        for result in case_results
        for check in result["checks"]
        if check["passed"]
    )
    return {
        "summary": {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "case_pass_rate": round(passed / total, 4) if total else 0.0,
            "total_checks": check_total,
            "passed_checks": check_passed,
            "check_pass_rate": round(check_passed / check_total, 4) if check_total else 0.0,
        },
        "cases": case_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local golden-case evals for the review engine.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Path to JSONL eval cases.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="Path to write JSON eval results.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.dataset)
    report = build_report(cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(
        "Eval complete: "
        f"{summary['passed_cases']}/{summary['total_cases']} cases passed, "
        f"{summary['passed_checks']}/{summary['total_checks']} checks passed."
    )
    return 0 if summary["failed_cases"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
