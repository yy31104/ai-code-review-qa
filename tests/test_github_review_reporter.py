from __future__ import annotations

import json
from pathlib import Path

import evals.run_local  # noqa: F401
from diff_index import parse_unified_diff
from github_review_reporter import (
    MAX_MESSAGE_CHARS,
    SUMMARY_MARKER,
    build_review_payload,
    escape_markdown,
    is_commentable,
    write_payload,
)
from schemas import Finding, ReviewResult, TestResult as ReviewTestResult


def _diff_index():
    return parse_unified_diff(
        "\n".join(
            [
                "diff --git a/a.py b/a.py",
                "--- a/a.py",
                "+++ b/a.py",
                "@@ -1,1 +1,3 @@",
                " existing",
                "+added_a",
                "+added_b",
                "diff --git a/b.py b/b.py",
                "--- a/b.py",
                "+++ b/b.py",
                "@@ -4,1 +4,2 @@",
                " existing",
                "+added_b",
            ]
        )
    )


def _finding(
    *,
    file: str | None = "a.py",
    line: int | None = 2,
    category: str = "possible_bug",
    severity: str = "medium",
    message: str = "Check inputs.",
) -> Finding:
    return Finding(
        file=file,
        line=line,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        message=message,
    )


def _review(findings: list[Finding]) -> ReviewResult:
    return ReviewResult(
        project_summary="summary",
        changed_files=["a.py", "b.py"],
        risk_level="Low",
        possible_bugs=[],
        missing_tests=[],
        suggested_test_cases=[],
        security_reliability_concerns=[],
        findings=findings,
        automated_test_results=ReviewTestResult(command="pytest", passed=True),
        recommended_actions=[],
        review_decision="looks_good",
        human_review_decision="developer should verify before merging",
    )


def test_is_commentable_accepts_valid_right_line() -> None:
    assert is_commentable(_diff_index(), _finding(file="a.py", line=2)) is True


def test_is_commentable_routes_line_none_to_summary() -> None:
    assert is_commentable(_diff_index(), _finding(file="a.py", line=None)) is False


def test_is_commentable_routes_unknown_file_to_summary() -> None:
    assert is_commentable(_diff_index(), _finding(file="missing.py", line=2)) is False


def test_is_commentable_routes_non_right_line_to_summary() -> None:
    assert is_commentable(_diff_index(), _finding(file="a.py", line=99)) is False


def test_is_commentable_routes_suggested_test_to_summary() -> None:
    finding = _finding(file="a.py", line=2, category="suggested_test")

    assert is_commentable(_diff_index(), finding) is False


def test_is_commentable_routes_recommended_action_to_summary() -> None:
    finding = _finding(file="a.py", line=2, category="recommended_action")

    assert is_commentable(_diff_index(), finding) is False


def test_escape_markdown_neutralizes_mentions() -> None:
    assert "@\u200bocto" in escape_markdown("@octo please review")


def test_escape_markdown_neutralizes_issue_autolinks() -> None:
    assert "#\u200b123" in escape_markdown("see #123")


def test_escape_markdown_neutralizes_javascript_link() -> None:
    escaped = escape_markdown("[x](javascript:alert(1))")

    assert "\\[x\\]" in escaped
    assert "](" not in escaped
    assert "javascript:alert(1)" in escaped


def test_escape_markdown_escapes_backticks_and_fences() -> None:
    escaped = escape_markdown("```code```\n`value`")

    assert "\\`\\`\\`code\\`\\`\\` \\`value\\`" == escaped


def test_escape_markdown_escapes_html_sensitive_text() -> None:
    escaped = escape_markdown("<script>alert('x')</script>")

    assert "&lt;script&gt;" in escaped
    assert "<script>" not in escaped


def test_escape_markdown_escapes_emphasis_characters() -> None:
    assert escape_markdown("*x* _y_") == "\\*x\\* \\_y\\_"


def test_escape_markdown_caps_length() -> None:
    escaped = escape_markdown("a" * (MAX_MESSAGE_CHARS + 100))

    assert len(escaped) <= MAX_MESSAGE_CHARS
    assert escaped.endswith("(truncated)")


def test_build_review_payload_shape_and_summary_counts() -> None:
    review = _review(
        [
            _finding(file="b.py", line=5, severity="low", message="Second inline."),
            _finding(file="a.py", line=2, severity="high", message="@team first inline."),
            _finding(file=None, line=None, category="recommended_action", severity="info", message="See #123."),
        ]
    )

    payload = build_review_payload(review, _diff_index(), head_sha="abc123")

    assert payload["event"] == "COMMENT"
    assert payload["commit_id"] == "abc123"
    assert [comment["path"] for comment in payload["comments"]] == ["a.py", "b.py"]
    assert payload["comments"][0]["line"] == 2
    assert payload["comments"][0]["side"] == "RIGHT"
    assert "@\u200bteam" in payload["comments"][0]["body"]
    body = payload["body"]
    assert SUMMARY_MARKER in body
    assert "Verdict: looks\\_good" in body
    assert "Risk level: Low" in body
    assert "Test status: passed" in body
    assert "Inline findings: 2" in body
    assert "Summary-routed findings: 1" in body
    assert "Human-in-the-loop note" in body
    assert "#### General" in body
    assert "#\u200b123" in body


def test_build_review_payload_is_deterministic() -> None:
    review = _review(
        [
            _finding(file="b.py", line=5, severity="low", message="Second inline."),
            _finding(file="a.py", line=3, severity="medium", message="Later inline."),
            _finding(file="a.py", line=2, severity="high", message="First inline."),
        ]
    )

    first = build_review_payload(review, _diff_index())
    second = build_review_payload(review, _diff_index())

    assert first == second
    assert [(item["path"], item["line"]) for item in first["comments"]] == [
        ("a.py", 2),
        ("a.py", 3),
        ("b.py", 5),
    ]


def test_write_payload_writes_valid_json(tmp_path: Path) -> None:
    path = write_payload({"event": "COMMENT", "body": "body", "comments": []}, tmp_path / "out" / "review.json")

    assert json.loads(path.read_text(encoding="utf-8"))["event"] == "COMMENT"
