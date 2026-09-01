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

    if "review_decision" in expected:
        checks.append(
            CheckResult(
                name="review_decision",
                passed=review.review_decision == expected["review_decision"],
                detail=f"expected {expected['review_decision']!r}, got {review.review_decision!r}",
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

    if "findings" in expected:
        checks.extend(_run_finding_checks(review, expected["findings"]))

    if not checks:
        checks.append(CheckResult(name="schema_valid", passed=True, detail="ReviewResult parsed successfully"))

    return checks


def _run_finding_checks(review: ReviewResult, expected: dict[str, Any]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    findings = list(review.findings)

    if "min_total" in expected:
        minimum = int(expected["min_total"])
        checks.append(
            CheckResult(
                name="findings:min_total",
                passed=len(findings) >= minimum,
                detail=f"expected at least {minimum}, got {len(findings)}",
            )
        )

    if "max_total" in expected:
        maximum = int(expected["max_total"])
        checks.append(
            CheckResult(
                name="findings:max_total",
                passed=len(findings) <= maximum,
                detail=f"expected at most {maximum}, got {len(findings)}",
            )
        )

    categories_present = expected.get("categories_present", [])
    actual_categories = {_finding_attr(finding, "category") for finding in findings}
    for category in categories_present:
        found = category in actual_categories
        checks.append(
            CheckResult(
                name=f"findings:category:{category}",
                passed=found,
                detail=f"category {category!r} {'found' if found else 'not found'}",
            )
        )

    if "rule_ids" in expected:
        # The exact multiset of rules the case must produce. Unlike a minimum
        # count this fails on a missed detection and on an extra one, so a case
        # measures precision and recall together.
        expected_rules = sorted(str(rule) for rule in expected["rule_ids"])
        actual_rules = sorted(
            str(_finding_attr(finding, "rule_id"))
            for finding in findings
            if _finding_attr(finding, "rule_id")
        )
        checks.append(
            CheckResult(
                name="findings:rule_ids",
                passed=actual_rules == expected_rules,
                detail=f"expected {expected_rules}, got {actual_rules}",
            )
        )

    if expected.get("require_evidence"):
        anchored = [finding for finding in findings if _finding_attr(finding, "line") is not None]
        missing = [
            finding for finding in anchored if not str(_finding_attr(finding, "evidence") or "").strip()
        ]
        checks.append(
            CheckResult(
                name="findings:require_evidence",
                passed=not missing and bool(anchored),
                detail=f"{len(anchored)} anchored finding(s), {len(missing)} without evidence",
            )
        )

    if "file_anchored" in expected:
        expected_file_anchor = bool(expected["file_anchored"])
        actual_file_anchor = any(bool(_finding_attr(finding, "file")) for finding in findings)
        checks.append(
            CheckResult(
                name="findings:file_anchored",
                passed=actual_file_anchor == expected_file_anchor,
                detail=f"expected file anchored {expected_file_anchor}, got {actual_file_anchor}",
            )
        )

    severity_at_least = expected.get("severity_at_least")
    if severity_at_least:
        checks.extend(_run_severity_checks(findings, severity_at_least))

    if "require_line_anchor" in expected:
        require_line_anchor = bool(expected["require_line_anchor"])
        has_line_anchor = any(_finding_attr(finding, "line") is not None for finding in findings)
        checks.append(
            CheckResult(
                name="findings:line_anchor",
                passed=has_line_anchor == require_line_anchor,
                detail=f"expected line anchor {require_line_anchor}, got {has_line_anchor}",
            )
        )

    for anchor in expected.get("line_anchors", []):
        file = str(anchor.get("file", ""))
        line = int(anchor.get("line", 0))
        category = anchor.get("category")
        rule_id = anchor.get("rule_id")
        evidence = anchor.get("evidence")
        matching = [
            finding
            for finding in findings
            if _finding_attr(finding, "file") == file
            and _finding_attr(finding, "line") == line
            and (category is None or _finding_attr(finding, "category") == category)
            and (rule_id is None or _finding_attr(finding, "rule_id") == rule_id)
            and (
                evidence is None
                or str(evidence).strip() == str(_finding_attr(finding, "evidence") or "").strip()
            )
        ]
        label = rule_id or category or "any"
        checks.append(
            CheckResult(
                name=f"findings:line_anchor:{file}:{line}:{label}",
                passed=bool(matching),
                detail=f"expected {label} finding at {file}:{line}",
            )
        )

    return checks


def _run_severity_checks(findings: list[Any], severity_at_least: Any) -> list[CheckResult]:
    if isinstance(severity_at_least, dict):
        checks: list[CheckResult] = []
        for category, minimum in severity_at_least.items():
            matching = [
                _finding_attr(finding, "severity")
                for finding in findings
                if _finding_attr(finding, "category") == category
            ]
            passed = any(_severity_rank(severity) >= _severity_rank(str(minimum)) for severity in matching)
            checks.append(
                CheckResult(
                    name=f"findings:severity_at_least:{category}",
                    passed=passed,
                    detail=f"expected {category} severity at least {minimum!r}, got {matching or 'none'}",
                )
            )
        return checks

    minimum = str(severity_at_least)
    passed = all(_severity_rank(_finding_attr(finding, "severity")) >= _severity_rank(minimum) for finding in findings)
    return [
        CheckResult(
            name="findings:severity_at_least",
            passed=passed,
            detail=f"expected all finding severities at least {minimum!r}",
        )
    ]


def _severity_rank(severity: str) -> int:
    return {"info": 0, "low": 1, "medium": 2, "high": 3}.get(str(severity), -1)


def _finding_attr(finding: Any, field: str) -> Any:
    if isinstance(finding, dict):
        return finding.get(field)
    return getattr(finding, field, None)


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
