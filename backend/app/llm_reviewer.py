from __future__ import annotations

from schemas import ReviewResult


def review_diff(diff: str, changed_files: list[str]) -> ReviewResult:
    """Return a structured code review.

    Runs in demo mode using structured AI review output.
    Replace mock_review_json with a real LLM call when integrating an API key.
    """
    return ReviewResult(**mock_review_json(diff, changed_files))


def mock_review_json(diff: str, changed_files: list[str]) -> dict:
    """Build the structured payload a real LLM call will replace."""
    risk_level = _estimate_risk(diff, changed_files)
    has_tests = any("test" in f.lower() for f in changed_files)

    non_test = [f.split("/")[-1] for f in changed_files if "test" not in f.lower()]
    primary = non_test[0] if non_test else (changed_files[0].split("/")[-1] if changed_files else "the changed file")

    if changed_files:
        summary = (
            f"Reviewed {len(changed_files)} changed file(s) via the AI review engine. "
            "Running in demo mode using structured AI review output."
        )
    else:
        summary = "No changed files were detected by git diff. Running in demo mode using structured AI review output."

    possible_bugs = [
        f"`{primary}`: validate inputs at function boundaries — check for None, empty values, and unexpected types.",
        "Error paths may fail silently; confirm each failure branch returns a descriptive message or raises a typed exception.",
    ]
    if not diff and changed_files:
        possible_bugs.append(
            "Files appear untracked in git — diff is empty, so line-level analysis is limited for this change set."
        )

    missing_tests = []
    if not has_tests:
        missing_tests.append(
            f"No test files detected in the changed set. Consider adding tests alongside `{primary}`."
        )
    missing_tests.append(
        "Boundary-value coverage is missing: add tests for min/max inputs, empty collections, and off-by-one conditions."
    )

    return {
        "project_summary": summary,
        "changed_files": changed_files,
        "risk_level": risk_level,
        "possible_bugs": possible_bugs,
        "missing_tests": missing_tests,
        "suggested_test_cases": [
            f"Happy-path: call the primary function in `{primary}` with valid inputs and assert the expected return value.",
            "Invalid-input: pass None, an empty string, or an out-of-range value and assert a clear error is raised.",
            "Regression: add a test that pins any behavior this change is specifically intended to fix or improve.",
        ],
        "security_reliability_concerns": [
            "Do not log secrets, tokens, or user-identifiable data — scrub sensitive fields before writing to any log sink.",
            "Wrap subprocess calls and external I/O in try/except to prevent unhandled exceptions from crashing the process.",
        ],
        "automated_test_results": {
            "project_type": "not run",
            "command": "",
            "passed": False,
            "exit_code": 0,
            "output": "",
            "error": None,
            "test_summary": "",
        },
        "recommended_actions": [
            "Address the possible bugs listed above before requesting a final review.",
            "Ensure all automated tests pass in CI before merging.",
            "Expand test coverage around the highest-risk code paths identified in this report.",
        ],
        "human_review_decision": _decision_for_risk(risk_level),
    }


def _estimate_risk(diff: str, changed_files: list[str]) -> str:
    lowered = f"{diff}\n{' '.join(changed_files)}".lower()
    risky_terms = ["auth", "password", "token", "payment", "delete", "subprocess", "sql"]

    if any(term in lowered for term in risky_terms):
        return "High"
    if len(changed_files) > 5:
        return "Medium"
    return "Low"


def _decision_for_risk(risk_level: str) -> str:
    if risk_level == "High":
        return "High-risk changes detected. A senior engineer must review this diff before it can be merged."
    if risk_level == "Medium":
        return "Medium-risk changes. Approve only after confirming that test coverage adequately exercises the modified paths."
    return "Low-risk changes. Safe to merge once all automated checks pass."
