from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_generates_summary_for_pr_merge_base_range(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "app.py").write_text(
        "def issue_session(user):\n"
        "    return user\n",
        encoding="utf-8",
    )
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")

    _git(repo, "checkout", "-b", "feature")
    (repo / "app.py").write_text(
        "def issue_session(user):\n"
        "    subprocess.run(f\"grant {user}\", shell=True)\n"
        "    return user\n",
        encoding="utf-8",
    )
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "feature change")
    feature_head = _git_stdout(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "main")
    (repo / "base_only.py").write_text("BASE_ONLY = True\n", encoding="utf-8")
    _git(repo, "add", "base_only.py")
    _git(repo, "commit", "-m", "base only change")
    merge_base = _git_stdout(repo, "merge-base", "main", feature_head)

    report_path = tmp_path / "review_report.html"
    summary_path = tmp_path / "summary-comment.json"
    env = os.environ.copy()
    env["AI_REVIEW_MODE"] = "demo"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "app" / "main.py"),
            "--repo",
            str(repo),
            "--base",
            merge_base,
            "--head",
            feature_head,
            "--output",
            str(report_path),
            "--emit-summary-comment",
            str(summary_path),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert report_path.exists()
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    body = summary["body"]
    assert "AI Code Review Summary" in body
    assert "<!-- ai-code-review-qa:summary -->" in body
    assert "Risk level: High" in body
    assert "app.py" in body
    assert "base_only.py" not in body
    # The summary carries a real, line-anchored finding, not generic advice.
    assert "shell=True" in body


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )


def _git_stdout(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return completed.stdout.strip()
