"""Check finding file, line, and quoted evidence against a parsed diff.

Rejected findings retain a reason for inspection in the report.
"""

from __future__ import annotations

from diff_index import DiffIndex, normalize_path
from schemas import Finding

FILE_NOT_IN_DIFF = "file_not_in_diff"
LINE_NOT_ADDED = "line_not_added"
MISSING_LINE_ANCHOR = "missing_line_anchor"
EVIDENCE_MISMATCH = "evidence_mismatch"
BINARY_FILE = "binary_file"


def _normalize_source(text: str) -> str:
    """Collapse whitespace so quoting differences do not decide grounding."""
    return " ".join(text.split())


def check_finding(finding: Finding, diff_index: DiffIndex) -> str | None:
    """Return a rejection reason for ``finding``, or None if it is grounded."""
    if not finding.file:
        # A finding with no file is a summary-level statement. There is nothing
        # to attribute it to, and the reporter never posts it inline.
        return None

    diff_file = diff_index.files.get(normalize_path(finding.file))
    if diff_file is None:
        return FILE_NOT_IN_DIFF
    if diff_file.is_binary:
        return BINARY_FILE

    if finding.line is None:
        return None
    if finding.line not in diff_file.right_lines:
        return LINE_NOT_ADDED if diff_file.right_lines else MISSING_LINE_ANCHOR

    if finding.evidence:
        quoted = _normalize_source(finding.evidence)
        actual = _normalize_source(diff_file.right_source.get(finding.line, ""))
        if quoted and quoted not in actual and actual not in quoted:
            return EVIDENCE_MISMATCH

    return None


def ground_findings(
    findings: list[Finding], diff_index: DiffIndex
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into (grounded, rejected).

    Rejected findings carry the reason in ``grounding_rejection``. Order is
    preserved within each list so repeated runs over the same input agree.
    """
    grounded: list[Finding] = []
    rejected: list[Finding] = []

    for finding in findings:
        reason = check_finding(finding, diff_index)
        if reason is None:
            grounded.append(finding)
            continue
        rejected.append(finding.model_copy(update={"grounding_rejection": reason}))

    return grounded, rejected
