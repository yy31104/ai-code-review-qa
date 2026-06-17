from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_emits_github_review_payload_without_network(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    (repo / "app.py").write_text("def issue_session(user):\n    return user\n", encoding="utf-8")
    _run(["git", "add", "app.py"], cwd=repo)
    _run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        cwd=repo,
    )
    (repo / "app.py").write_text(
        "def issue_session(user):\n    authToken = create_token(user.password)\n    return user\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "report.html"
    payload_path = tmp_path / "reports" / "github" / "review.json"
    env = os.environ.copy()
    env["AI_REVIEW_MODE"] = "demo"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "app" / "main.py"),
            "--repo",
            str(repo),
            "--output",
            str(output_path),
            "--emit-github-review",
            str(payload_path),
            "--head-sha",
            "abc123",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()
    assert payload_path.exists()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["event"] == "COMMENT"
    assert payload["commit_id"] == "abc123"
    assert isinstance(payload["body"], str)
    assert isinstance(payload["comments"], list)
    assert "GitHub review payload generated" in completed.stdout


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=True, timeout=30)
