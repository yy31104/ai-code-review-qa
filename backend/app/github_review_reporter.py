from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from diff_index import DiffIndex

try:
    from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX, SUMMARY_MARKER
except ImportError:  # pragma: no cover - package import fallback
    from .github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX, SUMMARY_MARKER
from schemas import Finding, ReviewResult


MAX_MESSAGE_CHARS = 1000
MAX_INLINE_COMMENTS = 5
INLINE_CONFIDENCE_FLOOR = 0.5
_TRUNCATION_MARKER = " ... (truncated)"
_SUMMARY_ONLY_CATEGORIES = {"suggested_test", "recommended_action"}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_INLINE_SEVERITIES = {"high", "medium"}


def escape_markdown(text: str) -> str:
    """Escape untrusted text before placing it in GitHub Markdown."""
    value = str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\s*\n\s*", " ", value).strip()
    value = _neutralize_leading_markdown(value)
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


def finding_fingerprint(finding: Finding) -> str:
    """Return a stable fingerprint for deduping one finding across pushes."""
    normalized_message = _normalize_message(finding.message)
    message_hash = _sha1_hex(normalized_message)
    parts = [
        _normalize_path(finding.file or ""),
        str(finding.side),
        str(finding.line or ""),
        str(finding.category),
        message_hash,
    ]
    return _sha1_hex("\x00".join(parts))


def route_inline_findings(
    review: ReviewResult,
    diff_index: DiffIndex,
    *,
    max_inline: int = MAX_INLINE_COMMENTS,
    confidence_floor: float = INLINE_CONFIDENCE_FLOOR,
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into inline candidates and summary-routed findings."""
    inline_candidates: list[Finding] = []
    summary_findings: list[Finding] = []

    for finding in review.findings:
        if _is_inline_candidate(finding, diff_index, confidence_floor=confidence_floor):
            inline_candidates.append(finding)
        else:
            summary_findings.append(finding)

    sorted_candidates = sorted(inline_candidates, key=_inline_sort_key)
    inline_limit = max(0, int(max_inline))
    inline_findings = sorted_candidates[:inline_limit]
    overflow_findings = sorted_candidates[inline_limit:]
    return inline_findings, summary_findings + overflow_findings


def build_review_payload(
    review: ReviewResult,
    diff_index: DiffIndex,
    *,
    head_sha: str | None = None,
) -> dict[str, Any]:
    """Build a GitHub create-review request payload without posting it."""
    inline_findings, _ = _partition_findings(review, diff_index)

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
        "body": build_summary_comment_body(review, diff_index),
        "comments": comments,
    }
    if head_sha is not None:
        payload["commit_id"] = head_sha

    return payload


def build_inline_review_payload(
    review: ReviewResult,
    diff_index: DiffIndex,
    *,
    head_sha: str | None = None,
    max_inline: int = MAX_INLINE_COMMENTS,
) -> dict[str, Any]:
    """Build a capped inline-review payload artifact without posting it."""
    inline_limit = max(0, int(max_inline))
    inline_findings, summary_findings = route_inline_findings(review, diff_index, max_inline=inline_limit)
    eligible_count = len(_sorted_inline_candidates(review, diff_index))
    overflow_count = max(0, eligible_count - inline_limit)

    comments = [
        {
            "path": _normalize_path(str(finding.file)),
            "line": finding.line,
            "side": "RIGHT",
            "body": _fingerprinted_inline_body(finding),
        }
        for finding in inline_findings
    ]

    payload: dict[str, Any] = {
        "event": "COMMENT",
        "body": _summary_body(
            review,
            summary_findings,
            inline_count=len(inline_findings),
            overflow_count=overflow_count,
            inline_cap=inline_limit,
        ),
        "comments": comments,
    }
    if head_sha is not None:
        payload["commit_id"] = head_sha

    return payload


def build_summary_comment_body(review: ReviewResult, diff_index: DiffIndex) -> str:
    """Build the marker-based summary comment body used by dry-run and posting flows."""
    inline_findings, summary_findings = _partition_findings(review, diff_index)
    return _summary_body(review, summary_findings, inline_count=len(inline_findings))


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


def _fingerprinted_inline_body(finding: Finding) -> str:
    fingerprint = finding_fingerprint(finding)
    return "\n".join(
        [
            escape_markdown(finding.message),
            "",
            f"{INLINE_FINGERPRINT_PREFIX}{fingerprint}{INLINE_FINGERPRINT_SUFFIX}",
        ]
    )


def _summary_body(
    review: ReviewResult,
    summary_findings: list[Finding],
    *,
    inline_count: int,
    overflow_count: int = 0,
    inline_cap: int | None = None,
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

    if overflow_count:
        cap_label = inline_cap if inline_cap is not None else inline_count
        lines.extend(
            [
                "",
                (
                    f"Inline overflow note: {overflow_count} eligible finding(s) exceeded "
                    f"the inline cap of {cap_label} and were routed to this summary."
                ),
            ]
        )

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


def _partition_findings(review: ReviewResult, diff_index: DiffIndex) -> tuple[list[Finding], list[Finding]]:
    inline_findings: list[Finding] = []
    summary_findings: list[Finding] = []

    for finding in review.findings:
        if is_commentable(diff_index, finding):
            inline_findings.append(finding)
        else:
            summary_findings.append(finding)

    return inline_findings, summary_findings


def _sorted_inline_candidates(review: ReviewResult, diff_index: DiffIndex) -> list[Finding]:
    return sorted(
        (
            finding
            for finding in review.findings
            if _is_inline_candidate(finding, diff_index, confidence_floor=INLINE_CONFIDENCE_FLOOR)
        ),
        key=_inline_sort_key,
    )


def _is_inline_candidate(
    finding: Finding,
    diff_index: DiffIndex,
    *,
    confidence_floor: float,
) -> bool:
    return (
        is_commentable(diff_index, finding)
        and str(finding.severity).lower() in _INLINE_SEVERITIES
        and float(finding.confidence) >= confidence_floor
    )


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
    truncated = value[:limit].rstrip()
    truncated = _trim_unsafe_suffix(truncated)
    return f"{truncated}{_TRUNCATION_MARKER}"


def _neutralize_leading_markdown(value: str) -> str:
    if re.match(r"^(?:#{1,6}(?:\s|$)|>|[-+](?:\s|$)|\d+[.)](?:\s|$))", value):
        return f"\u200b{value}"
    return value


def _trim_unsafe_suffix(value: str) -> str:
    while value.endswith("\\") or value.endswith(("@", "#")) or _has_partial_html_entity(value):
        value = value[:-1].rstrip()
    return value


def _has_partial_html_entity(value: str) -> bool:
    match = re.search(r"&[A-Za-z]{0,5}$", value)
    return bool(match)


def _normalize_message(message: str) -> str:
    return " ".join(str(message).split())


def _normalize_path(path: str) -> str:
    return path.strip().strip('"').replace("\\", "/")


def _sha1_hex(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()
