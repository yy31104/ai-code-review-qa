from __future__ import annotations

import re

import evals.run_local  # noqa: F401
import pytest
from diff_index import parse_unified_diff
from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX
from github_review_reporter import (
    MAX_INLINE_COMMENTS,
    build_inline_review_payload,
    finding_fingerprint,
    route_inline_findings,
)
from schemas import Finding, ReviewResult, TestResult as ReviewTestResult


def _diff_index():
    return parse_unified_diff(
        "\n".join(
            [
                "diff --git a/a.py b/a.py",
                "--- a/a.py",
                "+++ b/a.py",
                "@@ -1,1 +1,10 @@",
                " existing",
                "+added_2",
                "+added_3",
                "+added_4",
                "+added_5",
                "+added_6",
                "+added_7",
                "+added_8",
                "+added_9",
                "+added_10",
                "diff --git a/b.py b/b.py",
                "--- a/b.py",
                "+++ b/b.py",
                "@@ -4,1 +4,3 @@",
                " existing",
                "+added_5",
                "+added_6",
                "diff --git a/delete.py b/delete.py",
                "--- a/delete.py",
                "+++ /dev/null",
                "@@ -1,1 +0,0 @@",
                "-removed",
                "diff --git a/image.png b/image.png",
                "Binary files a/image.png and b/image.png differ",
            ]
        )
    )


def _finding(
    *,
    file: str | None = "a.py",
    line: int | None = 2,
    side: str = "RIGHT",
    category: str = "possible_bug",
    severity: str = "medium",
    confidence: float = 0.8,
    message: str = "Check inputs.",
) -> Finding:
    return Finding(
        file=file,
        line=line,
        side=side,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,
        message=message,
    )


def _review(findings: list[Finding]) -> ReviewResult:
    return ReviewResult(
        project_summary="summary",
        changed_files=["a.py", "b.py"],
        risk_level="Medium",
        findings=findings,
        automated_test_results=ReviewTestResult(command="pytest", passed=True),
        review_decision="review_recommended",
        human_review_decision="developer should verify before merging",
    )


def test_finding_fingerprint_is_deterministic_and_normalizes_message_whitespace() -> None:
    first = _finding(message="Check   inputs\nbefore merge.")
    second = _finding(message="Check inputs before merge.")

    assert finding_fingerprint(first) == finding_fingerprint(first)
    assert finding_fingerprint(first) == finding_fingerprint(second)


def test_finding_fingerprint_changes_when_anchor_or_category_or_message_changes() -> None:
    base = _finding()
    variants = [
        _finding(file="b.py"),
        _finding(side="LEFT"),
        _finding(line=3),
        _finding(category="security_reliability"),
        _finding(message="Different message."),
    ]

    fingerprints = {finding_fingerprint(base), *(finding_fingerprint(variant) for variant in variants)}

    assert len(fingerprints) == len(variants) + 1


def test_finding_fingerprint_excludes_head_sha() -> None:
    review = _review([_finding(message="Stable finding.")])

    first = build_inline_review_payload(review, _diff_index(), head_sha="sha-one")
    second = build_inline_review_payload(review, _diff_index(), head_sha="sha-two")

    assert first["commit_id"] == "sha-one"
    assert second["commit_id"] == "sha-two"
    assert _fingerprint_from_body(first["comments"][0]["body"]) == _fingerprint_from_body(
        second["comments"][0]["body"]
    )


@pytest.mark.parametrize(
    "finding",
    [
        _finding(line=None),
        _finding(file="missing.py"),
        _finding(line=99),
        _finding(file="delete.py", line=1, severity="high"),
        _finding(file="image.png", line=1, severity="high"),
        _finding(severity="low"),
        _finding(severity="info"),
        _finding(confidence=0.49),
        _finding(category="suggested_test", severity="high", confidence=1.0),
        _finding(category="recommended_action", severity="high", confidence=1.0),
    ],
)
def test_route_inline_findings_rejects_non_inline_candidates(finding: Finding) -> None:
    inline, summary = route_inline_findings(_review([finding]), _diff_index())

    assert inline == []
    assert summary == [finding]


def test_route_inline_findings_accepts_high_and_medium_commentable_findings() -> None:
    high = _finding(file="a.py", line=2, severity="high")
    medium = _finding(file="b.py", line=5, severity="medium")

    inline, summary = route_inline_findings(_review([medium, high]), _diff_index())

    assert inline == [high, medium]
    assert summary == []


def test_route_inline_findings_applies_hard_cap_and_routes_overflow_to_summary() -> None:
    findings = [_finding(line=line, severity="high", message=f"Finding {line}") for line in range(2, 9)]

    inline, summary = route_inline_findings(_review(findings), _diff_index())

    assert len(inline) == MAX_INLINE_COMMENTS
    assert [finding.line for finding in inline] == [2, 3, 4, 5, 6]
    assert [finding.line for finding in summary] == [7, 8]


def test_build_inline_review_payload_shape_and_overflow_summary() -> None:
    findings = [_finding(line=line, severity="high", message=f"Finding {line}") for line in range(2, 8)]
    review = _review(findings)

    payload = build_inline_review_payload(review, _diff_index(), head_sha="abc123")

    assert payload["event"] == "COMMENT"
    assert payload["commit_id"] == "abc123"
    assert len(payload["comments"]) == 5
    first_comment = payload["comments"][0]
    assert first_comment["path"] == "a.py"
    assert first_comment["line"] == 2
    assert first_comment["side"] == "RIGHT"
    assert INLINE_FINGERPRINT_PREFIX in first_comment["body"]
    assert INLINE_FINGERPRINT_SUFFIX in first_comment["body"]
    assert "Inline overflow note: 1 eligible finding(s) exceeded the inline cap of 5" in payload["body"]
    assert "Summary-routed findings: 1" in payload["body"]


def test_build_inline_review_payload_escapes_inline_body_text() -> None:
    finding = _finding(message="@team check <script> `value` and #123")

    payload = build_inline_review_payload(_review([finding]), _diff_index())
    body = payload["comments"][0]["body"]

    assert "@\u200bteam" in body
    assert "&lt;script&gt;" in body
    assert "\\`value\\`" in body
    assert "#\u200b123" in body
    assert "<script>" not in body


def _fingerprint_from_body(body: str) -> str:
    match = re.search(
        re.escape(INLINE_FINGERPRINT_PREFIX) + r"([0-9a-f]{40})" + re.escape(INLINE_FINGERPRINT_SUFFIX),
        body,
    )
    assert match is not None
    return match.group(1)
