from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TestResult(BaseModel):
    project_type: str = "unknown"
    command: str = ""
    passed: bool = False
    exit_code: int = 0
    output: str = ""
    error: Optional[str] = None
    test_summary: str = ""


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
    automated_test_results: TestResult = Field(default_factory=TestResult)
    recommended_actions: List[str] = Field(default_factory=list)
    human_review_decision: str
