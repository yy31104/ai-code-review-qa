from __future__ import annotations

import os
import re
from textwrap import dedent

from dotenv import load_dotenv

from diff_index import parse_unified_diff, resolve_anchor
from schemas import Finding, ReviewResult, TestResult


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
MAX_DIFF_CHARS = 20000
NEEDS_HUMAN_REVIEW = "needs_human_review"
REVIEW_RECOMMENDED = "review_recommended"
LOOKS_GOOD = "looks_good"
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
# Identifier-aware tokenizer: split on non-alphanumeric separators and on
# camelCase/PascalCase boundaries before lowercasing, so "authToken" yields
# {"auth", "token"} while "author" and "tokenizer" stay single tokens.
_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def derive_decision(risk_level: str, test_result: TestResult) -> tuple[str, str]:
    """Derive a deterministic review decision from risk and automated tests."""
    normalized_risk = risk_level.strip().lower()
    has_test_command = bool(test_result.command.strip())

    if has_test_command and not test_result.passed:
        return (
            NEEDS_HUMAN_REVIEW,
            "Automated tests failed, so a developer should review the test output, AI findings, and change context before merging.",
        )

    if normalized_risk == "high":
        return (
            NEEDS_HUMAN_REVIEW,
            "High-risk changes should receive human review; a developer should inspect the AI findings and automated test results before merging.",
        )

    if normalized_risk == "medium":
        return (
            REVIEW_RECOMMENDED,
            "Review is recommended for this medium-risk change; a developer should verify the AI findings and automated test results before merging.",
        )

    if normalized_risk == "low":
        return (
            LOOKS_GOOD,
            "Looks good from the deterministic risk and test signals, but a developer should still verify the AI findings before merging.",
        )

    return (
        NEEDS_HUMAN_REVIEW,
        "Risk level was not recognized, so a developer should review the AI findings and automated test results before merging.",
    )


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
                    Set findings to a concise list of structured finding
                    objects. Each finding must include category, severity,
                    confidence, message, side, and optional file/line when the
                    finding can be tied to a changed file. Use side RIGHT for
                    findings on added or modified code. Valid categories are
                    possible_bug, security_reliability, missing_test,
                    suggested_test, and recommended_action. Valid severities
                    are info, low, medium, and high.
                    Set review_decision from risk_level before tests run:
                    High -> needs_human_review, Medium -> review_recommended,
                    Low -> looks_good.
                    Keep human_review_decision human-in-the-loop and mention that
                    a developer should verify the AI findings before merging.

                    The automated_test_results field is a placeholder and will
                    be replaced by the CLI after tests run. The CLI will also
                    recompute review_decision and human_review_decision from
                    risk_level plus the real automated test result.
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
    parsed.review_decision, parsed.human_review_decision = derive_decision(
        parsed.risk_level,
        parsed.automated_test_results,
    )
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
    suggested_test_cases = [
        f"Happy-path: call the primary function in `{primary}` with valid inputs and assert the expected return value.",
        "Invalid-input: pass None, an empty string, or an out-of-range value and assert a clear error is raised.",
        "Regression: add a test that pins any behavior this change is specifically intended to fix or improve.",
    ]
    security_reliability_concerns = [
        "Do not log secrets, tokens, or user-identifiable data - scrub sensitive fields before writing to any log sink.",
        "Wrap subprocess calls and external I/O in try/except to prevent unhandled exceptions from crashing the process.",
    ]
    recommended_actions = [
        "Address the possible bugs listed above before requesting a final review.",
        "Ensure all automated tests pass in CI before merging.",
        "Expand test coverage around the highest-risk code paths identified in this report.",
    ]

    test_result = TestResult(
        project_type="not run",
        command="",
        passed=False,
        exit_code=0,
        output="",
        error=None,
        test_summary="",
    )
    test_payload = test_result.model_dump() if hasattr(test_result, "model_dump") else test_result.dict()
    review_decision, human_review_decision = derive_decision(risk_level, test_result)
    findings = _build_findings(
        diff=diff,
        changed_files=changed_files,
        risk_level=risk_level,
        possible_bugs=possible_bugs,
        security_reliability_concerns=security_reliability_concerns,
        missing_tests=missing_tests,
        suggested_test_cases=suggested_test_cases,
        recommended_actions=recommended_actions,
    )

    return {
        "project_summary": summary,
        "changed_files": changed_files,
        "risk_level": risk_level,
        "possible_bugs": possible_bugs,
        "missing_tests": missing_tests,
        "suggested_test_cases": suggested_test_cases,
        "security_reliability_concerns": security_reliability_concerns,
        "findings": [
            finding.model_dump() if hasattr(finding, "model_dump") else finding.dict()
            for finding in findings
        ],
        "automated_test_results": test_payload,
        "recommended_actions": recommended_actions,
        "review_decision": review_decision,
        "human_review_decision": human_review_decision,
    }


def _build_findings(
    *,
    diff: str,
    changed_files: list[str],
    risk_level: str,
    possible_bugs: list[str],
    security_reliability_concerns: list[str],
    missing_tests: list[str],
    suggested_test_cases: list[str],
    recommended_actions: list[str],
) -> list[Finding]:
    diff_index = parse_unified_diff(diff)
    primary_file = _primary_changed_file(changed_files)

    possible_bug_severity = "medium" if risk_level in {"High", "Medium"} else "low"
    security_severity = "high" if risk_level == "High" else "medium"
    findings: list[Finding] = []

    for message in possible_bugs:
        findings.append(
            Finding(
                file=primary_file,
                line=resolve_anchor(diff_index, primary_file),
                category="possible_bug",
                severity=possible_bug_severity,
                confidence=0.5,
                message=message,
            )
        )

    for message in security_reliability_concerns:
        findings.append(
            Finding(
                file=primary_file,
                line=resolve_anchor(diff_index, primary_file),
                category="security_reliability",
                severity=security_severity,
                confidence=0.6,
                message=message,
            )
        )

    for message in missing_tests:
        findings.append(
            Finding(
                file=primary_file,
                line=resolve_anchor(diff_index, primary_file),
                category="missing_test",
                severity="low",
                confidence=0.7,
                message=message,
            )
        )

    for message in suggested_test_cases:
        findings.append(
            Finding(
                file=None,
                line=None,
                category="suggested_test",
                severity="info",
                confidence=0.4,
                message=message,
            )
        )

    for message in recommended_actions:
        findings.append(
            Finding(
                file=None,
                line=None,
                category="recommended_action",
                severity="info",
                confidence=0.4,
                message=message,
            )
        )

    return findings


def _primary_changed_file(changed_files: list[str]) -> str | None:
    non_test_files = [path for path in changed_files if "test" not in path.lower()]
    if non_test_files:
        return non_test_files[0]
    if changed_files:
        return changed_files[0]
    return None


def _estimate_risk(diff: str, changed_files: list[str]) -> str:
    text = f"{diff}\n{' '.join(changed_files)}"
    tokens = _risk_tokens(text)

    if tokens.intersection(RISKY_TERMS) and not _all_changed_files_are_non_runtime(changed_files):
        return "High"
    if len(changed_files) > 5:
        return "Medium"
    return "Low"


def _risk_tokens(text: str) -> set[str]:
    """Split text into lowercased word tokens, honoring identifier casing.

    Separators (underscores, dots, spaces) break tokens, and camelCase or
    PascalCase humps are split too, so risk terms embedded in identifiers such
    as ``authToken`` or ``PaymentProcessor`` are detected. Because each
    lowercase run stays intact, lookalikes like ``author`` or ``tokenizer`` are
    not mistaken for ``auth`` or ``token``. Detection of risk terms fused into a
    single lowercase run (for example ``authtoken``) remains out of scope.
    """
    return {match.group(0).lower() for match in _IDENTIFIER_TOKEN_RE.finditer(text)}


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
