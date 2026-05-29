from __future__ import annotations

import os
import re
from textwrap import dedent

from dotenv import load_dotenv

from schemas import ReviewResult


HUMAN_REVIEW_EXPLANATION = (
    "AI findings and automated test results should be reviewed by a developer before merging."
)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
MAX_DIFF_CHARS = 20000
RISKY_TERMS = {
    "auth",
    "authentication",
    "authorize",
    "authorization",
    "password",
    "passwords",
    "token",
    "tokens",
    "payment",
    "payments",
    "delete",
    "subprocess",
    "sql",
}


def review_diff(diff: str, changed_files: list[str]) -> ReviewResult:
    """Return a structured code review.

    Demo mode is the default. Set AI_REVIEW_MODE=openai plus OPENAI_API_KEY
    to call the OpenAI Responses API.
    """
    load_dotenv()
    mode = os.getenv("AI_REVIEW_MODE", "demo").strip().lower()

    if mode in {"", "demo"}:
        return _demo_review(diff, changed_files)

    if mode != "openai":
        return _demo_review(
            diff,
            changed_files,
            warning=f"Unknown AI_REVIEW_MODE={mode!r}; falling back to demo mode.",
        )

    try:
        return _review_with_openai(diff, changed_files)
    except Exception as exc:
        return _demo_review(
            diff,
            changed_files,
            warning=f"OpenAI review failed ({exc}); falling back to demo mode.",
        )


def _demo_review(diff: str, changed_files: list[str], warning: str | None = None) -> ReviewResult:
    payload = mock_review_json(diff, changed_files)
    payload["review_mode"] = "demo"
    payload["review_model"] = None
    if warning:
        payload["project_summary"] = f"{payload['project_summary']} Warning: {warning}"
    return ReviewResult(**payload)


def _review_with_openai(diff: str, changed_files: list[str]) -> ReviewResult:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.responses.parse(
        model=model,
        input=[
            {
                "role": "system",
                "content": dedent(
                    """
                    You are an AI code review assistant. Return only structured
                    JSON matching the ReviewResult schema. Keep findings concise,
                    practical, and grounded in the provided git diff.

                    Set changed_files to the provided changed file list.
                    Set human_review_decision to:
                    AI findings and automated test results should be reviewed by a developer before merging.

                    The automated_test_results field is a placeholder and will
                    be replaced by the CLI after tests run.
                    """
                ).strip(),
            },
            {
                "role": "user",
                "content": dedent(
                    f"""
                    Changed files:
                    {changed_files}

                    Git diff:
                    {_truncate_diff(diff)}
                    """
                ).strip(),
            },
        ],
        text_format=ReviewResult,
    )

    parsed = response.output_parsed
    if not isinstance(parsed, ReviewResult):
        raise ValueError("OpenAI response did not parse into ReviewResult")

    parsed.review_mode = "openai"
    parsed.review_model = model
    return parsed


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
        f"`{primary}`: validate inputs at function boundaries - check for None, empty values, and unexpected types.",
        "Error paths may fail silently; confirm each failure branch returns a descriptive message or raises a typed exception.",
    ]
    if not diff and changed_files:
        possible_bugs.append(
            "Files appear untracked in git - diff is empty, so line-level analysis is limited for this change set."
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
            "Do not log secrets, tokens, or user-identifiable data - scrub sensitive fields before writing to any log sink.",
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
        "human_review_decision": HUMAN_REVIEW_EXPLANATION,
    }


def _estimate_risk(diff: str, changed_files: list[str]) -> str:
    lowered = f"{diff}\n{' '.join(changed_files)}".lower()
    # Whole-token matching reduces substring false positives like "author" or "tokenizer".
    # This deterministic demo heuristic may miss camelCase/PascalCase identifiers such
    # as authToken or deleteUser; camelCase-aware matching is a follow-up.
    tokens = set(re.findall(r"[a-z0-9]+", lowered))

    if tokens.intersection(RISKY_TERMS) and not _all_changed_files_are_non_runtime(changed_files):
        return "High"
    if len(changed_files) > 5:
        return "Medium"
    return "Low"


def _all_changed_files_are_non_runtime(changed_files: list[str]) -> bool:
    return bool(changed_files) and all(
        _is_test_file(path) or _is_documentation_file(path) for path in changed_files
    )


def _is_test_file(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    segments = set(lowered.split("/"))
    return (
        "tests" in segments
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_documentation_file(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    segments = set(lowered.split("/"))
    return "docs" in segments or lowered.endswith((".md", ".rst", ".txt", ".adoc"))


def _truncate_diff(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff or "(No diff content available.)"
    return f"{diff[:MAX_DIFF_CHARS]}\n\n[Diff truncated to {MAX_DIFF_CHARS} characters.]"
