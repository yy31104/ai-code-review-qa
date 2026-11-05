from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


FindingCategory = Literal[
    "possible_bug",
    "security_reliability",
    "missing_test",
    "suggested_test",
    "recommended_action",
]
FindingSeverity = Literal["info", "low", "medium", "high"]
ReviewStatus = Literal[
    "completed",
    # The provider returned a valid review, but it was only shown part of the
    # diff. Static rules still cover the whole diff; the decision is forced to
    # human review because the provider's silence on unseen files means nothing.
    "incomplete",
    "configuration_error",
    "provider_failed",
    "invalid_output",
    "abstained",
    "no_changes",
]
ReviewSource = Literal["provider", "static_rules", "none"]
# Where a finding's `confidence` number came from.
# - "rule": the fixed value a deterministic rule assigns to every match.
# - "model_self_report": the number the provider wrote about its own finding.
#   Verbalized confidence is poorly calibrated (Xiong et al., ICLR 2024), so it
#   is kept in artifacts for auditing but never shown in the report.
# - "sample_agreement": the share of independent provider samples over the same
#   request that reported this finding (see `sample_consistency.py`).
ConfidenceSource = Literal["rule", "model_self_report", "sample_agreement"]
REVIEW_FAILURE_STATUSES = frozenset({"configuration_error", "provider_failed", "invalid_output"})
REVIEW_NON_PUBLISHABLE_STATUSES = REVIEW_FAILURE_STATUSES | frozenset(
    {"abstained", "no_changes"}
)


class TestResult(BaseModel):
    project_type: str = "unknown"
    command: str = ""
    passed: bool = False
    exit_code: int = 0
    output: str = ""
    error: Optional[str] = None
    test_summary: str = ""


class Finding(BaseModel):
    file: Optional[str] = None
    line: Optional[int] = None
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    category: FindingCategory
    severity: FindingSeverity = "info"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    message: str
    # Identifier of the deterministic rule that produced this finding, or None
    # when a provider produced it. It is what makes a finding reproducible.
    rule_id: Optional[str] = None
    # The source line the finding is about, copied from the diff. Grounding
    # compares this against the saved diff; it proves provenance, not truth.
    evidence: Optional[str] = None
    # Why grounding rejected the finding, when it did. None means the finding
    # was either accepted or never submitted for grounding.
    grounding_rejection: Optional[str] = None
    confidence_source: Optional[ConfidenceSource] = None
    # Set only when confidence_source is "sample_agreement": how many of
    # `sample_count` provider samples reported this finding after grounding.
    sample_support: Optional[int] = None
    sample_count: Optional[int] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.5

        if numeric != numeric:
            return 0.5
        return min(1.0, max(0.0, numeric))


class ProposedFinding(BaseModel):
    """A single finding as a provider proposes it, before grounding.

    This is deliberately narrower than `Finding`: it holds only what a model can
    legitimately know from a diff. Pipeline-owned fields (rule_id, grounding
    outcome) are set by the caller after the response is validated, so the model
    cannot assert them.
    """

    file: Optional[str] = None
    line: Optional[int] = None
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    category: FindingCategory
    severity: FindingSeverity = "info"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    message: str
    evidence: Optional[str] = None

    def to_finding(self) -> "Finding":
        return Finding(
            file=self.file,
            line=self.line,
            side=self.side,
            category=self.category,
            severity=self.severity,
            confidence=self.confidence,
            message=self.message,
            rule_id=None,
            evidence=self.evidence,
            confidence_source="model_self_report",
        )


class ProviderReview(BaseModel):
    """The whole of what a provider response is allowed to contain.

    Risk level, review status, provenance, test results and the review decision
    are computed by the pipeline and are not in this schema, so model output can
    never move the merge gate on its own.
    """

    project_summary: str
    findings: List[ProposedFinding] = Field(default_factory=list)


class ReviewResult(BaseModel):
    review_mode: str = "static"
    review_status: ReviewStatus = "completed"
    review_source: ReviewSource = "static_rules"
    review_status_detail: Optional[str] = None
    review_model: Optional[str] = None
    # Number of provider samples drawn for this review. 1 is a single call; 2 or
    # more means provider confidence is sample agreement. None: no provider call.
    provider_samples: Optional[int] = None
    project_summary: str
    changed_files: List[str] = Field(default_factory=list)
    risk_level: str
    possible_bugs: List[str] = Field(default_factory=list)
    missing_tests: List[str] = Field(default_factory=list)
    suggested_test_cases: List[str] = Field(default_factory=list)
    security_reliability_concerns: List[str] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    # Findings that were produced but failed grounding. They are kept for
    # auditing and are never rendered as accepted findings or posted anywhere.
    rejected_findings: List[Finding] = Field(default_factory=list)
    automated_test_results: TestResult = Field(default_factory=TestResult)
    recommended_actions: List[str] = Field(default_factory=list)
    review_decision: str = "needs_human_review"
    human_review_decision: str
