from __future__ import annotations

import os
import re
from textwrap import dedent

from dotenv import load_dotenv
from pydantic import ValidationError

from diff_index import normalize_path, parse_unified_diff
from finding_grounding import ground_findings
from schemas import (
    REVIEW_FAILURE_STATUSES,
    Finding,
    ProviderReview,
    ReviewResult,
    ReviewStatus,
    TestResult,
)
from static_review import AnalysisResult, analyze_diff


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
MAX_DIFF_CHARS = 20000
# Bumped whenever the system prompt changes. Any evaluation result is only
# comparable with another result produced under the same prompt version.
REVIEW_PROMPT_VERSION = "2026-09-01.1"
MAX_SUMMARY_LIST_ITEMS = 10
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


class ReviewConfigurationError(RuntimeError):
    """Raised when provider mode was requested but is not configured."""


class InvalidReviewOutputError(ValueError):
    """Raised when a provider response is not a validated review result."""


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


def derive_final_decision(review: ReviewResult, test_result: TestResult) -> tuple[str, str]:
    """Apply provider-status gating before the deterministic risk/test decision."""
    if review.review_status in REVIEW_FAILURE_STATUSES:
        return (
            NEEDS_HUMAN_REVIEW,
            (
                f"The requested review did not complete ({review.review_status}). "
                "No model findings were produced; resolve the failure and rerun before merging."
            ),
        )
    if review.review_status == "abstained":
        return (
            NEEDS_HUMAN_REVIEW,
            "The reviewer abstained because the diff did not provide enough added-line evidence; a developer must review the change directly.",
        )
    if review.review_status == "no_changes":
        return (
            NEEDS_HUMAN_REVIEW,
            "No reviewable Python diff was available, so no automated judgment was made.",
        )
    return derive_decision(review.risk_level, test_result)


def review_diff(diff: str, changed_files: list[str]) -> ReviewResult:
    """Return a structured code review.

    Static mode is the default. Set AI_REVIEW_MODE=openai plus OPENAI_API_KEY
    to call the OpenAI Responses API.
    """
    load_dotenv()
    mode = os.getenv("AI_REVIEW_MODE", "static").strip().lower() or "static"

    if mode not in {"static", "openai"}:
        return _failed_review(
            diff=diff,
            changed_files=changed_files,
            mode=mode,
            status="configuration_error",
            detail="AI_REVIEW_MODE must be either 'static' or 'openai'.",
        )

    input_status = _input_status(diff, changed_files)
    if input_status is not None:
        status, detail = input_status
        return _non_completed_review(
            diff=diff,
            changed_files=changed_files,
            mode=mode,
            status=status,
            detail=detail,
        )

    if mode == "static":
        return _static_review(diff, changed_files)

    try:
        return _review_with_openai(diff, changed_files)
    except ReviewConfigurationError as exc:
        return _failed_review(
            diff=diff,
            changed_files=changed_files,
            mode="openai",
            status="configuration_error",
            detail=str(exc),
            model=_configured_openai_model(),
        )
    except (InvalidReviewOutputError, ValidationError):
        return _failed_review(
            diff=diff,
            changed_files=changed_files,
            mode="openai",
            status="invalid_output",
            detail="OpenAI returned output that did not validate as ProviderReview.",
            model=_configured_openai_model(),
        )
    except Exception as exc:
        return _failed_review(
            diff=diff,
            changed_files=changed_files,
            mode="openai",
            status="provider_failed",
            detail=f"OpenAI request failed with {type(exc).__name__}.",
            model=_configured_openai_model(),
        )


def _static_review(diff: str, changed_files: list[str]) -> ReviewResult:
    """Review the diff with deterministic rules only. No model is called.

    Every finding here comes from a named rule in `static_review` and is
    anchored to an added line with that line as evidence. A diff that matches
    no rule produces no findings; the reviewer stays silent rather than filling
    the report with generic advice.
    """
    analysis = analyze_diff(diff, changed_files)
    findings = [
        Finding(
            file=match.path,
            line=match.line,
            side="RIGHT",
            category=match.category,
            severity=match.severity,
            confidence=match.confidence,
            message=match.message,
            rule_id=match.rule_id,
            evidence=match.evidence,
        )
        for match in analysis.matches
    ]
    risk_level = escalate_risk(_estimate_risk(diff, changed_files), findings)
    review_decision, human_review_decision = derive_decision(risk_level, TestResult())

    return ReviewResult(
        review_mode="static",
        review_status="completed",
        review_source="static_rules",
        review_status_detail=None,
        review_model=None,
        project_summary=_summarize_analysis(analysis, changed_files),
        changed_files=changed_files,
        risk_level=risk_level,
        findings=findings,
        automated_test_results=TestResult(project_type="not run"),
        review_decision=review_decision,
        human_review_decision=human_review_decision,
        **project_finding_lists(findings, analysis=analysis),
    )


def _input_status(
    diff: str, changed_files: list[str]
) -> tuple[ReviewStatus, str] | None:
    """Classify input availability without using the finding count.

    The reviewer is scoped to Python additions. No diff or no Python path is a
    `no_changes` outcome. A Python path with no readable added right-side line
    is an explicit abstention because the evidence required for grounding is
    unavailable.
    """
    if not diff.strip():
        return "no_changes", "No diff content was available to review."

    diff_index = parse_unified_diff(diff)
    paths = {normalize_path(path) for path in changed_files}
    paths.update(diff_index.files)
    python_paths = {path for path in paths if path.lower().endswith(".py")}
    if not python_paths:
        return "no_changes", "The diff contained no Python files in the reviewer's scope."

    for path in python_paths:
        diff_file = diff_index.files.get(path)
        if diff_file is not None and not diff_file.is_binary and diff_file.right_lines:
            return None

    return (
        "abstained",
        "Python changes were present, but the diff contained no readable added lines to ground a review.",
    )


def _non_completed_review(
    *,
    diff: str,
    changed_files: list[str],
    mode: str,
    status: ReviewStatus,
    detail: str,
) -> ReviewResult:
    """Return a valid no-input or abstention result without findings."""
    test_result = TestResult(project_type="not run")
    review = ReviewResult(
        review_mode=mode,
        review_status=status,
        review_source="none",
        review_status_detail=detail,
        review_model=None,
        project_summary=detail,
        changed_files=changed_files,
        risk_level=_estimate_risk(diff, changed_files),
        automated_test_results=test_result,
        recommended_actions=(
            ["Provide a unified diff with readable added Python lines and rerun the reviewer."]
            if status == "abstained"
            else []
        ),
        review_decision=NEEDS_HUMAN_REVIEW,
        human_review_decision="pending",
    )
    review.review_decision, review.human_review_decision = derive_final_decision(
        review, test_result
    )
    return review


def _failed_review(
    *,
    diff: str,
    changed_files: list[str],
    mode: str,
    status: ReviewStatus,
    detail: str,
    model: str | None = None,
) -> ReviewResult:
    """Return an auditable failure artifact without substituting static findings."""
    test_result = TestResult(project_type="not run")
    return ReviewResult(
        review_mode=mode,
        review_status=status,
        review_source="none",
        review_status_detail=detail,
        review_model=model,
        project_summary=(
            f"The requested review did not complete ({status}). "
            "No model findings were produced."
        ),
        changed_files=changed_files,
        risk_level=_estimate_risk(diff, changed_files),
        automated_test_results=test_result,
        recommended_actions=[
            "Resolve the review status error and rerun the same diff.",
            "Do not treat this artifact as a completed provider-backed review.",
        ],
        review_decision=NEEDS_HUMAN_REVIEW,
        human_review_decision=(
            f"The requested review did not complete ({status}); "
            "a developer must review the diff directly."
        ),
    )


SYSTEM_PROMPT = dedent(
    """
    You review one git diff and propose findings for a human reviewer. You do
    not decide whether the change is safe to merge, and nothing you return is
    published without a person approving it.

    Rules for every finding you return:

    - Only report something you can point at in the provided diff. Set `file` to
      a path from the changed-file list and `line` to a line number that the
      diff adds on the right side.
    - Set `evidence` to the exact added line you are describing, copied
      verbatim from the diff. A finding whose evidence does not match that line
      is discarded by the caller.
    - Prefer a short list of specific findings over broad advice. Do not restate
      general good practice that this diff does not show a problem with. An
      empty findings list is the correct answer for a clean diff.
    - Valid categories: possible_bug, security_reliability, missing_test,
      suggested_test, recommended_action. Valid severities: info, low, medium,
      high. Set `confidence` to how sure you are that the finding is real.
    - Use side RIGHT for anything on added or modified code.

    `project_summary` is one or two sentences describing what the diff does.
    """
).strip()


def _review_with_openai(diff: str, changed_files: list[str]) -> ReviewResult:
    """Ask the provider for proposed findings, then ground them against the diff.

    The provider fills `ProviderReview` only: a summary and a list of proposed
    findings. Risk level, status, provenance and the review decision are
    computed here, so a model response cannot move the merge gate by itself.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ReviewConfigurationError("OPENAI_API_KEY is not set.")

    model = _configured_openai_model()

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
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
        text_format=ProviderReview,
    )

    parsed = response.output_parsed
    if not isinstance(parsed, ProviderReview):
        raise InvalidReviewOutputError("OpenAI response did not parse into ProviderReview")

    proposed = [finding.to_finding() for finding in parsed.findings]
    grounded, rejected = ground_findings(proposed, parse_unified_diff(diff))

    risk_level = escalate_risk(_estimate_risk(diff, changed_files), grounded)
    review = ReviewResult(
        review_mode="openai",
        review_status="completed",
        review_source="provider",
        review_status_detail=_grounding_detail(grounded, rejected),
        review_model=model,
        project_summary=parsed.project_summary,
        changed_files=changed_files,
        risk_level=risk_level,
        findings=grounded,
        rejected_findings=rejected,
        automated_test_results=TestResult(project_type="not run"),
        review_decision=NEEDS_HUMAN_REVIEW,
        human_review_decision="pending",
        **project_finding_lists(grounded),
    )
    review.review_decision, review.human_review_decision = derive_final_decision(
        review,
        review.automated_test_results,
    )
    return review


def _grounding_detail(grounded: list[Finding], rejected: list[Finding]) -> str | None:
    """Describe grounding losses so a clean-looking run is not silently thin."""
    if not rejected:
        return None
    reasons = sorted({finding.grounding_rejection or "unknown" for finding in rejected})
    return (
        f"Grounding kept {len(grounded)} of {len(grounded) + len(rejected)} proposed "
        f"findings; rejected: {', '.join(reasons)}."
    )


def _configured_openai_model() -> str:
    return os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


_LIST_FIELD_BY_CATEGORY = {
    "possible_bug": "possible_bugs",
    "security_reliability": "security_reliability_concerns",
    "missing_test": "missing_tests",
    "suggested_test": "suggested_test_cases",
}
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


def _locate(finding: Finding) -> str:
    if finding.file and finding.line is not None:
        return f"{finding.file}:{finding.line}"
    if finding.file:
        return finding.file
    return "change set"


def project_finding_lists(
    findings: list[Finding],
    *,
    analysis: AnalysisResult | None = None,
) -> dict[str, list[str]]:
    """Project findings onto the flat lists the report renders.

    `findings` is the single source of truth. These lists are a view of it, so
    the report can never show a concern that is not backed by a finding.
    """
    lists: dict[str, list[str]] = {
        "possible_bugs": [],
        "security_reliability_concerns": [],
        "missing_tests": [],
        "suggested_test_cases": [],
    }

    for finding in findings:
        field = _LIST_FIELD_BY_CATEGORY.get(finding.category)
        if field is None:
            continue
        lists[field].append(f"{_locate(finding)} - {finding.message}")

    for finding in findings:
        if finding.category != "missing_test":
            continue
        lists["suggested_test_cases"].append(
            f"{_locate(finding)} - add a test that covers at least one failure path "
            "of the code added here."
        )

    for field, items in lists.items():
        lists[field] = items[:MAX_SUMMARY_LIST_ITEMS]

    lists["recommended_actions"] = _recommended_actions(findings, analysis)
    return lists


def _recommended_actions(
    findings: list[Finding], analysis: AnalysisResult | None
) -> list[str]:
    """Derive next steps from what was actually found, not from a fixed list."""
    actions: list[str] = []

    high = [finding for finding in findings if finding.severity == "high"]
    if high:
        actions.append(
            f"Resolve or explicitly accept {len(high)} high-severity finding(s) before merging."
        )

    action_findings = [finding for finding in findings if finding.category == "recommended_action"]
    actions.extend(f"{_locate(finding)} - {finding.message}" for finding in action_findings)

    if analysis is not None and analysis.unanchorable_files:
        listed = ", ".join(analysis.unanchorable_files[:5])
        actions.append(
            f"No diff content is available for {listed}; findings in those files cannot be "
            "anchored to a line."
        )

    if not findings:
        actions.append(
            "No deterministic rule matched this diff. That is not a pass: a human still "
            "has to read the change."
        )

    return actions[:MAX_SUMMARY_LIST_ITEMS]


def escalate_risk(risk_level: str, findings: list[Finding]) -> str:
    """Raise the risk level to match the most severe finding actually produced.

    Risk started as a keyword match over the diff text, which misses a real
    defect whose file and identifiers happen to contain no risk term: an SQL
    statement built by interpolation in `repo.py` scored Low while the rule
    engine flagged it as high severity, so the gate said `looks_good`.
    Escalation only ever moves risk up, so a finding can add human review but
    can never remove it.
    """
    ranked = {"low": 0, "medium": 1, "high": 2}
    current = ranked.get(risk_level.strip().lower(), 0)

    highest = highest_severity(findings)
    from_findings = {"info": 0, "low": 0, "medium": 1, "high": 2}.get(highest, 0)

    return ["Low", "Medium", "High"][max(current, from_findings)]


def _summarize_analysis(analysis: AnalysisResult, changed_files: list[str]) -> str:
    """State exactly what was inspected and what matched."""
    if not changed_files:
        return "No changed files were detected. Deterministic rules ran over an empty change set."

    matched_rules = sorted({match.rule_id for match in analysis.matches})
    inspected = (
        f"Deterministic rules inspected {analysis.inspected_added_lines} added line(s) "
        f"across {analysis.inspected_files} file(s) of {len(changed_files)} changed file(s). "
        "No model was called."
    )
    if not matched_rules:
        return f"{inspected} No rule matched."
    return f"{inspected} Matched rules: {', '.join(matched_rules)}."


def highest_severity(findings: list[Finding]) -> str:
    """Return the highest severity present, or 'info' when there are none."""
    if not findings:
        return "info"
    return max(findings, key=lambda finding: _SEVERITY_RANK.get(finding.severity, -1)).severity


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
