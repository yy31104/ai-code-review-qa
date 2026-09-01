from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Put backend/app on sys.path in the same way as the other application tests.
import evals.run_local  # noqa: F401
import main as cli_main
from schemas import ReviewResult, TestResult as ReviewTestResult


@pytest.mark.parametrize(
    ("status", "source", "expected_exit", "publishes"),
    [
        ("completed", "static_rules", 0, True),
        ("configuration_error", "none", 2, False),
        ("provider_failed", "none", 2, False),
        ("invalid_output", "none", 2, False),
        ("abstained", "none", 0, False),
        ("no_changes", "none", 0, False),
    ],
)
def test_status_controls_exit_code_and_github_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: str,
    source: str,
    expected_exit: int,
    publishes: bool,
) -> None:
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,2 @@\n"
        " value = 1\n"
        "+other = 2\n"
    )
    review = ReviewResult(
        review_mode="static" if status in {"completed", "abstained", "no_changes"} else "openai",
        review_status=status,
        review_source=source,
        review_status_detail=None if status == "completed" else f"{status} detail",
        project_summary="Status smoke test.",
        changed_files=["app.py"],
        risk_level="Low",
        human_review_decision="A developer should verify the result.",
    )
    monkeypatch.setattr(
        cli_main,
        "read_git_diff",
        lambda *_args, **_kwargs: SimpleNamespace(
            diff=diff,
            changed_files=["app.py"],
            error=None,
        ),
    )
    monkeypatch.setattr(
        cli_main,
        "run_tests",
        lambda _repo: ReviewTestResult(project_type="not run"),
    )
    monkeypatch.setattr(cli_main, "review_diff", lambda *_args, **_kwargs: review)

    report = tmp_path / "report.html"
    artifacts = [
        tmp_path / "review.json",
        tmp_path / "summary.json",
        tmp_path / "inline.json",
        tmp_path / "fingerprints.json",
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py",
            "--repo",
            str(tmp_path),
            "--output",
            str(report),
            "--emit-github-review",
            str(artifacts[0]),
            "--emit-summary-comment",
            str(artifacts[1]),
            "--emit-inline-review",
            str(artifacts[2]),
            "--emit-finding-fingerprints",
            str(artifacts[3]),
        ],
    )

    assert cli_main.main() == expected_exit
    assert report.exists()
    assert all(path.exists() for path in artifacts) is publishes
