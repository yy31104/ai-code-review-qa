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
