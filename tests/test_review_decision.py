from __future__ import annotations

from pathlib import Path

import pytest

# Importing the eval runner puts backend/app on sys.path, matching the existing
# test setup for app modules.
import evals.run_local  # noqa: F401
from llm_reviewer import derive_decision, derive_final_decision
from report_generator import generate_report
from schemas import ReviewResult, TestResult as ReviewTestResult


@pytest.mark.parametrize(
    ("risk_level", "test_result", "expected_decision"),
    [
        ("Low", ReviewTestResult(command="python -m pytest", passed=True), "looks_good"),
        ("Low", ReviewTestResult(command="python -m pytest", passed=False), "needs_human_review"),
        ("Low", ReviewTestResult(command="", passed=False), "looks_good"),
        ("Medium", ReviewTestResult(command="python -m pytest", passed=True), "review_recommended"),
        ("Medium", ReviewTestResult(command="python -m pytest", passed=False), "needs_human_review"),
        ("Medium", ReviewTestResult(command="", passed=False), "review_recommended"),
        ("High", ReviewTestResult(command="python -m pytest", passed=True), "needs_human_review"),
        ("High", ReviewTestResult(command="python -m pytest", passed=False), "needs_human_review"),
        ("High", ReviewTestResult(command="", passed=False), "needs_human_review"),
    ],
)
def test_derive_decision_uses_risk_and_test_result(
    risk_level: str,
    test_result: ReviewTestResult,
    expected_decision: str,
) -> None:
    decision, explanation = derive_decision(risk_level, test_result)

    assert decision == expected_decision
    assert "developer" in explanation
    assert "before merging" in explanation


def test_low_passing_report_renders_lgtm_decision(tmp_path: Path) -> None:
    test_result = ReviewTestResult(
        project_type="python",
        command="python -m pytest",
        passed=True,
        exit_code=0,
        output="1 passed",
        test_summary="1 passed",
    )
    review = ReviewResult(
        project_summary="Low-risk settings change.",
        changed_files=["backend/app/settings.py"],
        risk_level="Low",
        possible_bugs=[],
        missing_tests=[],
        suggested_test_cases=[],
        security_reliability_concerns=[],
        automated_test_results=test_result,
        recommended_actions=[],
        human_review_decision="placeholder",
    )
    review.review_decision, review.human_review_decision = derive_decision(
        review.risk_level,
        test_result,
    )

    report_path = generate_report(review, tmp_path / "report.html")
    html = report_path.read_text(encoding="utf-8")

    assert "badge badge-lgtm" in html
    assert "Decision: Looks Good - Verify &amp; Merge" in html
    assert "aria-label=\"Looks Good - Verify &amp; Merge decision icon\"" in html
    assert "Decision: Needs Human Review" not in html
    assert "Deterministic demo output." in html
    assert "No model was called." in html


def test_provider_failure_forces_human_review_and_renders_provenance(tmp_path: Path) -> None:
    test_result = ReviewTestResult(command="python -m pytest", passed=True)
    review = ReviewResult(
        review_mode="openai",
        review_status="provider_failed",
        review_source="none",
        review_status_detail="OpenAI request failed with TimeoutError.",
        project_summary="The requested review did not complete.",
        changed_files=["backend/app/settings.py"],
        risk_level="Low",
        automated_test_results=test_result,
        human_review_decision="placeholder",
    )

    review.review_decision, review.human_review_decision = derive_final_decision(review, test_result)
    report_path = generate_report(review, tmp_path / "failed-report.html")
    html = report_path.read_text(encoding="utf-8")

    assert review.review_decision == "needs_human_review"
    assert "Review did not complete." in html
    assert "OpenAI request failed with TimeoutError." in html
    assert "No provider findings were produced." in html
