from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from diff_index import DiffIndex
from schemas import Finding, ReviewResult


SUMMARY_MARKER = "<!-- ai-code-review-qa:summary -->"
MAX_MESSAGE_CHARS = 1000
_TRUNCATION_MARKER = " ... (truncated)"
_SUMMARY_ONLY_CATEGORIES = {"suggested_test", "recommended_action"}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def escape_markdown(text: str) -> str:
    """Escape untrusted text before placing it in GitHub Markdown."""
    value = str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\s*\n\s*", " ", value).strip()
    value = value.replace("](", "]\\(")
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    for character in ("`", "[", "]", "*", "_"):
        value = value.replace(character, f"\\{character}")

    value = value.replace("@", "@\u200b")
    value = re.sub(r"#(?=\d)", "#\u200b", value)
    return _truncate_message(value)


def is_commentable(diff_index: DiffIndex, finding: Finding) -> bool:
    """Return whether a finding can be emitted as a GitHub inline comment."""
    if finding.category in _SUMMARY_ONLY_CATEGORIES:
        return False
    if not finding.file or finding.line is None:
        return False

    file_key = _normalize_path(finding.file)
    diff_file = diff_index.files.get(file_key)
    if diff_file is None:
        return False

    return finding.line in diff_file.right_lines


def build_review_payload(
    review: ReviewResult,
    diff_index: DiffIndex,
    *,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """Build a GitHub create-review request payload without posting it."""
    inline_findings: list[Finding] = []
    summary_findings: list[Finding] = []

    for finding in review.findings:
        if is_commentable(diff_index, finding):
            inline_findings.append(finding)
        else:
            summary_findings.append(finding)

    comments = [
        {
            "path": _normalize_path(str(finding.file)),
            "line": finding.line,
            "side": "RIGHT",
            "body": _inline_body(finding),
        }
        for finding in sorted(inline_findings, key=_inline_sort_key)
    ]

    payload: dict[str, Any] = {
        "event": "COMMENT",
        "body": _summary_body(review, summary_findings, inline_count=len(comments)),
        "comments": comments,
    }
    if head_sha is not None:
        payload["commit_id"] = head_sha

    return payload


def write_payload(payload: dict[str, Any], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return output_path


def _inline_body(finding: Finding) -> str:
    return "\n".join(
        [
            f"Severity: {escape_markdown(finding.severity)}",
            f"Category: {escape_markdown(finding.category)}",
            "",
            escape_markdown(finding.message),
        ]
    )


def _summary_body(
    review: ReviewResult,
    summary_findings: list[Finding],
    *,
    inline_count: int,
) -> str:
    lines = [
        SUMMARY_MARKER,
        "## AI Code Review Summary",
        "",
        f"- Verdict: {escape_markdown(review.review_decision)}",
        f"- Human review explanation: {escape_markdown(review.human_review_decision)}",
        f"- Risk level: {escape_markdown(review.risk_level)}",
        f"- Test status: {escape_markdown(_test_status(review))}",
        f"- Inline findings: {inline_count}",
        f"- Summary-routed findings: {len(summary_findings)}",
        "",
        "Human-in-the-loop note: this dry-run payload is advisory only; a developer must verify findings before merging.",
    ]

    lines.extend(["", "### Summary-routed findings"])
    if not summary_findings:
        lines.append("None.")
        return "\n".join(lines)

    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in sorted(summary_findings, key=_summary_sort_key):
        grouped[finding.file or "General"].append(finding)

    for file_label in sorted(grouped, key=_group_sort_key):
        lines.extend(["", f"#### {escape_markdown(file_label)}"])
        for finding in grouped[file_label]:
            label = f"{escape_markdown(finding.severity)} / {escape_markdown(finding.category)}"
            lines.append(f"- {label}: {escape_markdown(finding.message)}")

    return "\n".join(lines)


def _test_status(review: ReviewResult) -> str:
    test_result = review.automated_test_results
    if not test_result.command.strip():
        return "not run"
    if test_result.passed:
        return f"passed ({test_result.command})"
    return f"failed ({test_result.command})"


def _inline_sort_key(finding: Finding) -> tuple[str, int, int, str, str]:
    return (
        _normalize_path(finding.file or ""),
        int(finding.line or 0),
        _severity_rank(finding.severity),
        finding.category,
        finding.message,
    )


def _summary_sort_key(finding: Finding) -> tuple[str, int, str, int, str]:
    return (
        _normalize_path(finding.file or ""),
        _severity_rank(finding.severity),
        finding.category,
        int(finding.line or 0),
        finding.message,
    )


def _group_sort_key(file_label: str) -> tuple[int, str]:
    return (0 if file_label == "General" else 1, file_label)


def _severity_rank(severity: str) -> int:
    return _SEVERITY_ORDER.get(str(severity), len(_SEVERITY_ORDER))


def _truncate_message(value: str) -> str:
    if len(value) <= MAX_MESSAGE_CHARS:
        return value

    limit = MAX_MESSAGE_CHARS - len(_TRUNCATION_MARKER)
    return f"{value[:limit].rstrip()}{_TRUNCATION_MARKER}"


def _normalize_path(path: str) -> str:
    return path.strip().strip('"').replace("\\", "/")
