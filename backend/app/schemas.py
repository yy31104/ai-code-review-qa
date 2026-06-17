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


class ReviewResult(BaseModel):
    review_mode: str = "demo"
    review_model: Optional[str] = None
    project_summary: str
    changed_files: List[str] = Field(default_factory=list)
    risk_level: str
    possible_bugs: List[str] = Field(default_factory=list)
    missing_tests: List[str] = Field(default_factory=list)
    suggested_test_cases: List[str] = Field(default_factory=list)
    security_reliability_concerns: List[str] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    automated_test_results: TestResult = Field(default_factory=TestResult)
    recommended_actions: List[str] = Field(default_factory=list)
    review_decision: str = "needs_human_review"
    human_review_decision: str
