from __future__ import annotations

# Importing the eval runner puts backend/app on sys.path, matching the existing
# test modules in this directory.
import evals.run_local  # noqa: F401
from diff_index import parse_unified_diff
from finding_grounding import ground_findings
from schemas import Finding

DIFF = (
    "diff --git a/backend/app/auth.py b/backend/app/auth.py\n"
    "--- a/backend/app/auth.py\n+++ b/backend/app/auth.py\n"
    "@@ -4,1 +4,2 @@\n"
    " def issue_token(user):\n"
    "+    return build(user.password)\n"
)
INDEX = parse_unified_diff(DIFF)


def finding(**overrides: object) -> Finding:
    payload: dict[str, object] = {
        "file": "backend/app/auth.py",
        "line": 5,
        "category": "possible_bug",
        "severity": "medium",
        "confidence": 0.5,
        "message": "message",
    }
    payload.update(overrides)
    return Finding(**payload)  # type: ignore[arg-type]


def test_added_line_with_matching_evidence_is_grounded() -> None:
    grounded, rejected = ground_findings(
        [finding(evidence="    return build(user.password)")], INDEX
    )

    assert len(grounded) == 1
    assert rejected == []


def test_whitespace_differences_do_not_reject_a_finding() -> None:
    grounded, rejected = ground_findings([finding(evidence="return  build(user.password)")], INDEX)

    assert len(grounded) == 1
    assert rejected == []


def test_partial_quote_of_the_line_is_grounded() -> None:
    grounded, _ = ground_findings([finding(evidence="build(user.password)")], INDEX)

    assert len(grounded) == 1


def test_line_outside_the_diff_is_rejected() -> None:
    _, rejected = ground_findings([finding(line=900)], INDEX)

    assert rejected[0].grounding_rejection == "line_not_added"


def test_context_line_is_rejected_because_the_diff_did_not_add_it() -> None:
    _, rejected = ground_findings([finding(line=4)], INDEX)

    assert rejected[0].grounding_rejection == "line_not_added"


def test_file_outside_the_diff_is_rejected() -> None:
    _, rejected = ground_findings([finding(file="backend/app/other.py")], INDEX)

    assert rejected[0].grounding_rejection == "file_not_in_diff"


def test_evidence_that_does_not_match_the_line_is_rejected() -> None:
    _, rejected = ground_findings([finding(evidence="os.system('rm -rf /')")], INDEX)

    assert rejected[0].grounding_rejection == "evidence_mismatch"


def test_file_level_finding_needs_only_a_file_in_the_diff() -> None:
    grounded, rejected = ground_findings([finding(line=None)], INDEX)

    assert len(grounded) == 1
    assert rejected == []


def test_summary_level_finding_without_a_file_is_kept() -> None:
    grounded, rejected = ground_findings([finding(file=None, line=None)], INDEX)

    assert len(grounded) == 1
    assert rejected == []


def test_grounding_does_not_mutate_the_input_findings() -> None:
    original = finding(line=900)
    _, rejected = ground_findings([original], INDEX)

    assert original.grounding_rejection is None
    assert rejected[0] is not original


# --- Evidence must identify the line, not merely occur in it -------------------

import pytest  # noqa: E402

from finding_grounding import (  # noqa: E402
    MIN_EVIDENCE_CHARS,
    MIN_EVIDENCE_COVERAGE,
    evidence_rejection,
)

LONG_LINE = "    result = subprocess.run(command, shell=True, check=False, capture_output=True)"
SHORT_LINE = "retry = 3"
MIXED_DIFF = (
    "diff --git a/svc.py b/svc.py\n"
    "--- a/svc.py\n+++ b/svc.py\n"
    "@@ -1,1 +1,6 @@\n"
    " import subprocess\n"
    f"+{LONG_LINE}\n"
    "+\n"
    f"+{SHORT_LINE}\n"
    "+handle(\n"
    "+)\n"
)
MIXED_INDEX = parse_unified_diff(MIXED_DIFF)


def svc_finding(line: int, evidence: str | None) -> Finding:
    return finding(file="svc.py", line=line, evidence=evidence)


def reason(line: int, evidence: str | None) -> str | None:
    grounded, rejected = ground_findings([svc_finding(line, evidence)], MIXED_INDEX)
    if grounded:
        return None
    return rejected[0].grounding_rejection


def test_thresholds_are_the_documented_values() -> None:
    assert MIN_EVIDENCE_CHARS == 20
    assert MIN_EVIDENCE_COVERAGE == 0.5


@pytest.mark.parametrize(
    "quote",
    ["(", "user", "build(user", "password)"],
)
def test_fragment_that_occurs_in_the_line_is_too_short(quote: str) -> None:
    _, rejected = ground_findings([finding(evidence=quote)], INDEX)

    assert rejected[0].grounding_rejection == "evidence_too_short"


def test_long_line_accepts_a_distinctive_twenty_character_quote() -> None:
    quote = "shell=True, check=Fa"
    assert len(quote) == MIN_EVIDENCE_CHARS
    assert len(quote) < MIN_EVIDENCE_COVERAGE * len(" ".join(LONG_LINE.split()))

    assert reason(2, quote) is None


def test_long_line_rejects_a_nineteen_character_quote_below_half_coverage() -> None:
    assert reason(2, "shell=True, check=F") == "evidence_too_short"


def test_long_line_rejects_a_common_short_token() -> None:
    assert reason(2, "shell=True") == "evidence_too_short"


def test_short_line_quoted_in_full_is_grounded() -> None:
    assert reason(4, "retry = 3") is None


def test_short_line_quoted_at_half_coverage_is_grounded() -> None:
    # "retry" is 5 of the 9 normalised characters.
    assert reason(4, "retry") is None


def test_short_line_rejects_a_quote_below_half_coverage() -> None:
    assert reason(4, "3") == "evidence_too_short"
    assert reason(4, "= 3") == "evidence_too_short"


def test_blank_added_line_no_longer_grounds_any_quote() -> None:
    """Before: '' is a substring of every string, so any quote matched a blank line."""
    assert reason(3, "os.system(user_supplied_command)") == "evidence_mismatch"


def test_one_character_line_inside_a_long_quote_is_a_mismatch() -> None:
    """Before: the line ')' occurs in the quote, so the quote was accepted."""
    assert reason(6, "result = eval(user_supplied_expression)") == "evidence_mismatch"


def test_quote_that_includes_the_diff_marker_is_grounded() -> None:
    assert reason(2, "+" + LONG_LINE) is None


def test_quote_spanning_this_line_and_the_next_is_grounded() -> None:
    assert reason(2, LONG_LINE + "\n" + SHORT_LINE) is None


@pytest.mark.parametrize("evidence", [None, "", "   \t"])
def test_line_anchored_finding_without_a_quote_is_rejected(evidence: str | None) -> None:
    assert reason(2, evidence) == "missing_evidence"


def test_file_level_finding_still_needs_no_quote() -> None:
    grounded, rejected = ground_findings([svc_finding(None, None)], MIXED_INDEX)  # type: ignore[arg-type]

    assert len(grounded) == 1
    assert rejected == []


@pytest.mark.parametrize(
    ("line_source", "quote", "expected"),
    [
        # Coverage boundary on a 10-character line: 5 characters pass, 4 do not.
        ("abcdefghij", "abcde", None),
        ("abcdefghij", "abcd", "evidence_too_short"),
        # Length boundary on a 100-character line: 20 characters pass, 19 do not.
        ("x" * 100, "x" * 20, None),
        ("x" * 100, "x" * 19, "evidence_too_short"),
        # Over-long quote: the line must be 20 characters or half of the quote.
        ("abcde", "abcde" + "z" * 5, None),
        ("abcde", "abcde" + "z" * 6, "evidence_mismatch"),
        ("y" * 20, "y" * 20 + "z" * 200, None),
        ("", "anything", "evidence_mismatch"),
    ],
)
def test_evidence_rejection_boundaries(
    line_source: str, quote: str, expected: str | None
) -> None:
    assert evidence_rejection(quote, line_source) == expected
