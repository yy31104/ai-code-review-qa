from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import evals.run_local as run_local
from evals.run_local import _run_checks
from llm_reviewer import _review_with_openai, review_diff
from report_generator import generate_report
from schemas import Finding, ReviewResult, TestResult as ReviewTestResult


VALID_CATEGORIES = {
    "possible_bug",
    "security_reliability",
    "missing_test",
    "suggested_test",
    "recommended_action",
}
VALID_SEVERITIES = {"info", "low", "medium", "high"}


def _dump_findings(review: ReviewResult) -> list[dict[str, object]]:
    return [
        finding.model_dump() if hasattr(finding, "model_dump") else finding.dict()
        for finding in review.findings
    ]


def _review_with_one_finding(findings: list[Finding] | None = None) -> ReviewResult:
    return ReviewResult(
        project_summary="summary",
        changed_files=["backend/app/example.py"],
        risk_level="Low",
        possible_bugs=[],
        missing_tests=[],
        suggested_test_cases=[],
        security_reliability_concerns=[],
        automated_test_results=ReviewTestResult(command="pytest", passed=True),
        recommended_actions=[],
        findings=findings or [],
        review_decision="looks_good",
        human_review_decision="developer should verify before merging",
    )


def test_finding_schema_validation_and_confidence_clamp() -> None:
    finding = Finding(
        file="backend/app/auth.py",
        line=12,
        side="RIGHT",
        category="security_reliability",
        severity="high",
        confidence=0.8,
        message="Do not log tokens.",
    )

    assert finding.file == "backend/app/auth.py"
    assert finding.line == 12
    assert finding.side == "RIGHT"
    assert finding.confidence == 0.8
    assert Finding(category="possible_bug", confidence=1.7, message="x").confidence == 1.0
    assert Finding(category="possible_bug", confidence=-1, message="x").confidence == 0.0
    assert Finding(category="possible_bug", confidence="not-a-number", message="x").confidence == 0.5


def test_review_result_defaults_findings_to_empty_list() -> None:
    review = ReviewResult(
        project_summary="summary",
        changed_files=[],
        risk_level="Low",
        human_review_decision="developer should verify before merging",
    )

    assert review.findings == []


def test_demo_findings_are_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    diff = "diff --git a/backend/app/auth.py b/backend/app/auth.py\n+authToken = build(user)\n"
    changed_files = ["backend/app/auth.py"]

    first = review_diff(diff, changed_files)
    second = review_diff(diff, changed_files)

    assert _dump_findings(first) == _dump_findings(second)


def test_auth_diff_yields_security_reliability_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff(
        "diff --git a/backend/app/auth.py b/backend/app/auth.py\n+authToken = build(user)\n",
        ["backend/app/auth.py"],
    )

    assert any(finding.category == "security_reliability" for finding in review.findings)


def test_demo_finding_metadata_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff("+def issue_token(user):\n+    return user.password\n", ["backend/app/auth.py"])

    assert review.findings
    for finding in review.findings:
        assert finding.category in VALID_CATEGORIES
        assert finding.severity in VALID_SEVERITIES
        assert 0.0 <= finding.confidence <= 1.0
        assert finding.side == "RIGHT"


def test_empty_diff_without_files_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff("", [])

    assert review.findings
    assert all(finding.file is None for finding in review.findings)


def test_realistic_hunk_diff_can_produce_line_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    diff = "\n".join(
        [
            "diff --git a/backend/app/auth.py b/backend/app/auth.py",
            "--- a/backend/app/auth.py",
            "+++ b/backend/app/auth.py",
            "@@ -10,2 +10,3 @@",
            " def existing():",
            "+    authToken = build(user)",
        ]
    )

    review = review_diff(diff, ["backend/app/auth.py"])

    assert any(finding.file == "backend/app/auth.py" and finding.line == 11 for finding in review.findings)


def test_headerless_diff_falls_back_to_file_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff("+authToken = build(user)\n", ["backend/app/auth.py"])

    file_findings = [finding for finding in review.findings if finding.file == "backend/app/auth.py"]
    assert file_findings
    assert all(finding.line is None for finding in file_findings)


def test_eval_finding_checks_can_pass_and_fail() -> None:
    review = _review_with_one_finding(
        [
            Finding(
                file="backend/app/example.py",
                line=4,
                category="possible_bug",
                severity="medium",
                confidence=0.5,
                message="Check inputs.",
            )
        ]
    )

    passing = _run_checks(
        review,
        {
            "findings": {
                "min_total": 1,
                "categories_present": ["possible_bug"],
                "file_anchored": True,
                "severity_at_least": {"possible_bug": "low"},
                "require_line_anchor": True,
            }
        },
    )
    failing = _run_checks(review, {"findings": {"categories_present": ["missing_test"]}})

    assert all(check.passed for check in passing)
    assert not all(check.passed for check in failing)


def test_report_renders_anchored_findings_and_escapes_messages(tmp_path: Path) -> None:
    review = _review_with_one_finding(
        [
            Finding(
                file="backend/app/example.py",
                line=7,
                category="possible_bug",
                severity="medium",
                confidence=0.5,
                message="<script>alert('x')</script>",
            )
        ]
    )

    report_path = generate_report(review, tmp_path / "report.html")
    html = report_path.read_text(encoding="utf-8")

    assert "Anchored Findings" in html
    assert "backend/app/example.py" in html
    assert "line 7" in html
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in html
    assert "<script>alert" not in html


def test_report_survives_empty_findings(tmp_path: Path) -> None:
    report_path = generate_report(_review_with_one_finding([]), tmp_path / "report.html")
    html = report_path.read_text(encoding="utf-8")

    assert "Anchored Findings" in html
    assert "0 item(s)" in html
    assert "None reported." in html


def test_mocked_openai_path_preserves_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = _review_with_one_finding(
        [
            Finding(
                file="backend/app/auth.py",
                line=5,
                category="security_reliability",
                severity="high",
                confidence=0.6,
                message="Token handling changed.",
            )
        ]
    )
    seen: dict[str, object] = {}

    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            seen["text_format"] = kwargs.get("text_format")
            return SimpleNamespace(output_parsed=parsed)

    class FakeOpenAI:
        def __init__(self, api_key: str) -> None:
            seen["api_key"] = api_key
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    review = _review_with_openai("+authToken = build(user)", ["backend/app/auth.py"])

    assert seen == {"api_key": "test-key", "text_format": ReviewResult}
    assert review.review_mode == "openai"
    assert review.findings[0].category == "security_reliability"
    assert review.findings[0].line == 5


def test_mocked_openai_parse_failure_falls_back_to_demo_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(output_parsed={"not": "a review"})

    class FakeOpenAI:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    monkeypatch.setenv("AI_REVIEW_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    review = review_diff("+authToken = build(user)", ["backend/app/auth.py"])

    assert review.review_mode == "demo"
    assert review.findings
    assert any(finding.category == "security_reliability" for finding in review.findings)
    assert "falling back to demo mode" in review.project_summary
