from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import evals.run_local as run_local
from evals.run_local import _run_checks
from llm_reviewer import _review_with_openai, review_diff
from report_generator import generate_report
from schemas import (
    Finding,
    ProposedFinding,
    ProviderReview,
    ReviewResult,
    TestResult as ReviewTestResult,
)


VALID_CATEGORIES = {
    "possible_bug",
    "security_reliability",
    "missing_test",
    "suggested_test",
    "recommended_action",
}
VALID_SEVERITIES = {"info", "low", "medium", "high"}

# A diff whose added line 11 matches exactly one deterministic rule.
SHELL_TRUE_DIFF = "\n".join(
    [
        "diff --git a/backend/app/deploy.py b/backend/app/deploy.py",
        "--- a/backend/app/deploy.py",
        "+++ b/backend/app/deploy.py",
        "@@ -10,1 +10,2 @@",
        " def existing():",
        "+    subprocess.run(command, shell=True)",
        "",
    ]
)

# A diff whose added line 5 is what the mocked provider claims to describe.
AUTH_DIFF = "\n".join(
    [
        "diff --git a/backend/app/auth.py b/backend/app/auth.py",
        "--- a/backend/app/auth.py",
        "+++ b/backend/app/auth.py",
        "@@ -4,1 +4,2 @@",
        " def issue_token(user):",
        "+    return build(user.password)",
        "",
    ]
)


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
    changed_files = ["backend/app/deploy.py"]

    first = review_diff(SHELL_TRUE_DIFF, changed_files)
    second = review_diff(SHELL_TRUE_DIFF, changed_files)

    assert first.findings
    assert _dump_findings(first) == _dump_findings(second)
    assert first.review_status == "demo"
    assert first.review_source == "demo_rules"


def test_shell_true_yields_grounded_security_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff(SHELL_TRUE_DIFF, ["backend/app/deploy.py"])

    matching = [finding for finding in review.findings if finding.rule_id == "subprocess_shell_true"]
    assert len(matching) == 1
    finding = matching[0]
    assert finding.category == "security_reliability"
    assert finding.severity == "high"
    assert (finding.file, finding.line) == ("backend/app/deploy.py", 11)
    assert finding.evidence == "subprocess.run(command, shell=True)"


def test_clean_diff_produces_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    clean_diff = "\n".join(
        [
            "diff --git a/backend/app/totals.py b/backend/app/totals.py",
            "--- a/backend/app/totals.py",
            "+++ b/backend/app/totals.py",
            "@@ -3,1 +3,2 @@",
            " def _total(rows):",
            "+    return sum(row.amount for row in rows)",
            "",
        ]
    )

    review = review_diff(clean_diff, ["backend/app/totals.py", "tests/test_totals.py"])

    assert review.findings == []
    assert review.possible_bugs == []
    assert "No rule matched" in review.project_summary


def test_demo_finding_metadata_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff(SHELL_TRUE_DIFF, ["backend/app/deploy.py"])

    assert review.findings
    for finding in review.findings:
        assert finding.category in VALID_CATEGORIES
        assert finding.severity in VALID_SEVERITIES
        assert 0.0 <= finding.confidence <= 1.0
        assert finding.side == "RIGHT"
        assert finding.rule_id
        assert finding.evidence


def test_empty_diff_without_files_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff("", [])

    assert review.findings == []
    assert review.rejected_findings == []
    assert "empty change set" in review.project_summary


def test_realistic_hunk_diff_can_produce_line_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff(SHELL_TRUE_DIFF, ["backend/app/deploy.py"])

    assert any(
        finding.file == "backend/app/deploy.py" and finding.line == 11
        for finding in review.findings
    )


def test_headerless_diff_produces_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without hunk headers there is no file or line to attribute a finding to."""
    monkeypatch.setenv("AI_REVIEW_MODE", "demo")
    review = review_diff("+subprocess.run(cmd, shell=True)\n", ["backend/app/auth.py"])

    assert review.findings == []
    assert any("cannot be anchored" in action for action in review.recommended_actions)


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


def _fake_openai(monkeypatch: pytest.MonkeyPatch, parsed: object) -> dict[str, object]:
    """Install a stub `openai` module that returns ``parsed`` from responses.parse."""
    seen: dict[str, object] = {}

    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            seen["text_format"] = kwargs.get("text_format")
            seen["model"] = kwargs.get("model")
            return SimpleNamespace(output_parsed=parsed)

    class FakeOpenAI:
        def __init__(self, api_key: str) -> None:
            seen["api_key"] = api_key
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    return seen


def test_mocked_openai_path_keeps_grounded_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = ProviderReview(
        project_summary="Token issuing changed.",
        findings=[
            ProposedFinding(
                file="backend/app/auth.py",
                line=5,
                category="security_reliability",
                severity="high",
                confidence=0.6,
                message="The raw password is passed into token construction.",
                evidence="    return build(user.password)",
            )
        ],
    )
    seen = _fake_openai(monkeypatch, parsed)

    review = _review_with_openai(AUTH_DIFF, ["backend/app/auth.py"])

    assert seen["api_key"] == "test-key"
    assert seen["text_format"] is ProviderReview
    assert review.review_mode == "openai"
    assert review.review_status == "completed"
    assert review.review_source == "provider"
    assert review.rejected_findings == []
    assert len(review.findings) == 1
    assert review.findings[0].line == 5
    assert review.findings[0].rule_id is None
    # The flat report lists are a projection of findings, never a second source.
    assert review.security_reliability_concerns == [
        "backend/app/auth.py:5 - The raw password is passed into token construction."
    ]


def test_provider_finding_off_the_diff_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = ProviderReview(
        project_summary="Token issuing changed.",
        findings=[
            ProposedFinding(
                file="backend/app/auth.py",
                line=900,
                category="possible_bug",
                severity="high",
                confidence=0.9,
                message="Line 900 dereferences None.",
                evidence="return None.value",
            )
        ],
    )
    _fake_openai(monkeypatch, parsed)

    review = _review_with_openai(AUTH_DIFF, ["backend/app/auth.py"])

    assert review.findings == []
    assert len(review.rejected_findings) == 1
    assert review.rejected_findings[0].grounding_rejection == "line_not_added"
    assert "Grounding kept 0 of 1" in (review.review_status_detail or "")


def test_provider_finding_with_wrong_evidence_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = ProviderReview(
        project_summary="Token issuing changed.",
        findings=[
            ProposedFinding(
                file="backend/app/auth.py",
                line=5,
                category="possible_bug",
                severity="medium",
                confidence=0.8,
                message="This line deletes the user record.",
                evidence="db.execute('DELETE FROM users')",
            )
        ],
    )
    _fake_openai(monkeypatch, parsed)

    review = _review_with_openai(AUTH_DIFF, ["backend/app/auth.py"])

    assert review.findings == []
    assert review.rejected_findings[0].grounding_rejection == "evidence_mismatch"


def test_mocked_openai_parse_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert review.review_mode == "openai"
    assert review.review_status == "invalid_output"
    assert review.review_source == "none"
    assert review.findings == []
    assert review.review_decision == "needs_human_review"
    assert "No model findings were produced" in review.project_summary


def test_openai_provider_failure_does_not_expose_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            raise RuntimeError("sensitive-provider-detail")

    class FakeOpenAI:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    monkeypatch.setenv("AI_REVIEW_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))

    review = review_diff("+authToken = build(user)", ["backend/app/auth.py"])

    assert review.review_status == "provider_failed"
    assert review.review_source == "none"
    assert review.findings == []
    assert review.review_status_detail == "OpenAI request failed with RuntimeError."
    assert "sensitive-provider-detail" not in review.review_status_detail
