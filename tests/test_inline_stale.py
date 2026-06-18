from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import evals.run_local  # noqa: F401
from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX
from github_inline_stale import build_stale_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"

FINGERPRINT_ONE = "a" * 40
FINGERPRINT_TWO = "b" * 40


def _body(fingerprint: str) -> str:
    return f"Finding body\n{INLINE_FINGERPRINT_PREFIX}{fingerprint}{INLINE_FINGERPRINT_SUFFIX}"


def _comment(
    comment_id: int,
    body: str,
    *,
    path: str = "a.py",
    line: int = 2,
    author: str = "github-actions[bot]",
) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": body,
        "path": path,
        "line": line,
        "user": {"login": author},
    }


def test_stale_flagged_when_marker_fingerprint_absent() -> None:
    plan = build_stale_plan([_comment(1, _body(FINGERPRINT_ONE))], {FINGERPRINT_TWO})

    assert plan == {
        "stale": [
            {
                "comment_id": 1,
                "fingerprint": FINGERPRINT_ONE,
                "path": "a.py",
                "line": 2,
                "author": "github-actions[bot]",
            }
        ],
        "stale_count": 1,
        "considered_count": 1,
    }


def test_not_stale_when_fingerprint_is_present() -> None:
    plan = build_stale_plan([_comment(1, _body(FINGERPRINT_ONE))], [FINGERPRINT_ONE])

    assert plan["stale"] == []
    assert plan["stale_count"] == 0
    assert plan["considered_count"] == 1


def test_comments_without_marker_are_ignored() -> None:
    comments = [
        _comment(1, "ordinary bot comment"),
        _comment(2, "ordinary human comment", author="alice"),
    ]

    plan = build_stale_plan(comments, [])

    assert plan == {"stale": [], "stale_count": 0, "considered_count": 0}


def test_human_comment_with_marker_is_considered_because_marker_defines_ownership() -> None:
    plan = build_stale_plan([_comment(1, _body(FINGERPRINT_ONE), author="alice")], [])

    assert plan["stale"][0]["author"] == "alice"
    assert plan["stale_count"] == 1
    assert plan["considered_count"] == 1


def test_duplicate_fingerprints_are_handled_safely() -> None:
    duplicate_body = "\n".join([_body(FINGERPRINT_ONE), _body(FINGERPRINT_ONE)])

    plan = build_stale_plan([_comment(1, duplicate_body), _comment(2, _body(FINGERPRINT_ONE))], [])

    assert plan["stale_count"] == 2
    assert [entry["comment_id"] for entry in plan["stale"]] == [1, 2]
    assert plan["considered_count"] == 2


def test_records_comment_metadata() -> None:
    plan = build_stale_plan(
        [_comment(7, _body(FINGERPRINT_ONE), path="src/app.py", line=42, author="github-actions[bot]")],
        [],
    )

    assert plan["stale"][0] == {
        "comment_id": 7,
        "fingerprint": FINGERPRINT_ONE,
        "path": "src/app.py",
        "line": 42,
        "author": "github-actions[bot]",
    }


def test_empty_inputs_are_safe() -> None:
    assert build_stale_plan([], []) == {"stale": [], "stale_count": 0, "considered_count": 0}


def test_build_stale_plan_does_not_mutate_input() -> None:
    comments = [_comment(1, _body(FINGERPRINT_ONE))]
    original = copy.deepcopy(comments)

    build_stale_plan(comments, [])

    assert comments == original


def test_cli_prints_stale_plan(tmp_path: Path) -> None:
    existing_path = tmp_path / "existing-review-comments.json"
    fingerprints_path = tmp_path / "finding-fingerprints.json"
    existing_path.write_text(json.dumps([_comment(1, _body(FINGERPRINT_ONE))]), encoding="utf-8")
    fingerprints_path.write_text(json.dumps({"fingerprints": []}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(APP_DIR / "github_inline_stale.py"),
            "--existing",
            str(existing_path),
            "--fingerprints",
            str(fingerprints_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    plan = json.loads(completed.stdout)
    assert plan["stale_count"] == 1
    assert plan["stale"][0]["comment_id"] == 1


def test_github_inline_stale_import_is_stdlib_only() -> None:
    script = f"""
import json
import sys
sys.path.insert(0, {str(APP_DIR)!r})
for name in ["pydantic", "schemas", "backend.app.schemas", "github_review_reporter", "backend.app.github_review_reporter"]:
    sys.modules.pop(name, None)
import github_inline_stale  # noqa: F401
print(json.dumps({{
    name: name in sys.modules
    for name in ["pydantic", "schemas", "backend.app.schemas", "github_review_reporter", "backend.app.github_review_reporter"]
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    imported = json.loads(completed.stdout)
    assert imported == {
        "pydantic": False,
        "schemas": False,
        "backend.app.schemas": False,
        "github_review_reporter": False,
        "backend.app.github_review_reporter": False,
    }
