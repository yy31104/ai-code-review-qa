from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import evals.run_local  # noqa: F401
from github_comment_upsert import decide_comment_action
from github_review_reporter import SUMMARY_MARKER


REPO_ROOT = Path(__file__).resolve().parents[1]


def _comment(
    comment_id: int,
    body: str,
    *,
    login: str = "github-actions[bot]",
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": body,
        "user": {"login": login},
        "created_at": created_at,
    }


def test_no_marker_creates_comment() -> None:
    assert decide_comment_action([_comment(1, "ordinary comment")]) == ("create", None)


def test_one_marker_patches_that_id() -> None:
    assert decide_comment_action([_comment(7, SUMMARY_MARKER)]) == ("patch", 7)


def test_multiple_markers_patch_oldest_and_warn(capsys) -> None:
    action = decide_comment_action(
        [
            _comment(2, SUMMARY_MARKER, created_at="2026-01-02T00:00:00Z"),
            _comment(1, SUMMARY_MARKER, created_at="2026-01-01T00:00:00Z"),
        ]
    )

    assert action == ("patch", 1)
    warning = capsys.readouterr().err
    assert "Warning:" in warning
    assert "1" in warning and "2" in warning


def test_multiple_markers_prefer_bot_author_even_when_non_bot_is_older(capsys) -> None:
    action = decide_comment_action(
        [
            _comment(1, SUMMARY_MARKER, login="alice", created_at="2026-01-01T00:00:00Z"),
            _comment(2, SUMMARY_MARKER, login="github-actions[bot]", created_at="2026-01-02T00:00:00Z"),
        ]
    )

    assert action == ("patch", 2)
    assert "Duplicate IDs: 1, 2" in capsys.readouterr().err


def test_marker_with_non_bot_author_patches_when_only_match() -> None:
    action = decide_comment_action(
        [_comment(4, f"user text\n{SUMMARY_MARKER}", login="alice")]
    )

    assert action == ("patch", 4)


def test_realistic_github_comment_json_shape() -> None:
    comments = [
        {
            "url": "https://api.github.com/repos/owner/repo/issues/comments/10",
            "html_url": "https://github.com/owner/repo/pull/1#issuecomment-10",
            "issue_url": "https://api.github.com/repos/owner/repo/issues/1",
            "id": 10,
            "node_id": "IC_kwDO",
            "user": {"login": "github-actions[bot]", "id": 41898282, "type": "Bot"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "body": f"{SUMMARY_MARKER}\n## AI Code Review Summary",
        }
    ]

    assert decide_comment_action(comments) == ("patch", 10)


def test_cli_prints_create_or_patch(tmp_path: Path) -> None:
    comments_path = tmp_path / "comments.json"
    comments_path.write_text(json.dumps([_comment(12, SUMMARY_MARKER)]), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "app" / "github_comment_upsert.py"),
            "--comments",
            str(comments_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    assert completed.stdout.strip() == "patch 12"
