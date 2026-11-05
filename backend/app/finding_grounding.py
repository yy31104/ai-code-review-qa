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
EVIDENCE_TOO_SHORT = "evidence_too_short"
MISSING_EVIDENCE = "missing_evidence"
BINARY_FILE = "binary_file"

# A quote that is only part of the line must be long enough to identify that
# line. It passes if it has at least MIN_EVIDENCE_CHARS characters, or if it
# covers at least MIN_EVIDENCE_COVERAGE of the line. Both are measured after
# whitespace is collapsed. The OR keeps short lines groundable: `x = 1` can only
# be quoted with five characters. On this repository's own Python, a random
# 20-character slice of one line occurs in a different line of the same file
# about 0.1-0.9% of the time, against 2-6% for 4-8 characters.
MIN_EVIDENCE_CHARS = 20
MIN_EVIDENCE_COVERAGE = 0.5


def _normalize_source(text: str) -> str:
    """Collapse whitespace so quoting differences do not decide grounding."""
    return " ".join(text.split())


def evidence_rejection(evidence: str | None, line_source: str) -> str | None:
    """Return why ``evidence`` does not identify ``line_source``, or None if it does.

    The quote may be part of the line (a partial quote) or the line may be part of
    the quote (the model copied a little extra, such as the ``+`` marker or the
    next line). Either way the overlapping text is the line content both sides
    agree on, and it must pass the length-or-coverage test above.
    """
    quoted = _normalize_source(evidence or "")
    if not quoted:
        return MISSING_EVIDENCE

    actual = _normalize_source(line_source)
    if quoted in actual:
        # Partial quote: the quote must be enough of the line to identify it.
        if len(quoted) >= MIN_EVIDENCE_CHARS or len(quoted) >= MIN_EVIDENCE_COVERAGE * len(actual):
            return None
        return EVIDENCE_TOO_SHORT

    if actual and actual in quoted:
        # Over-long quote: the line must be enough of the quote that the quote is
        # about this line, not about text the line merely appears inside.
        if len(actual) >= MIN_EVIDENCE_CHARS or len(actual) >= MIN_EVIDENCE_COVERAGE * len(quoted):
            return None

    return EVIDENCE_MISMATCH


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

    return evidence_rejection(finding.evidence, diff_file.right_source.get(finding.line, ""))


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
