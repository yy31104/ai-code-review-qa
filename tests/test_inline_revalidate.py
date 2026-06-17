from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import evals.run_local  # noqa: F401
from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX
from github_inline_revalidate import MINIMAL_REVIEW_BODY, revalidate_inline_payload
from github_inline_upsert import select_new_inline_comments


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"


def _diff() -> str:
    return "\n".join(
        [
            "diff --git a/a.py b/a.py",
            "--- a/a.py",
            "+++ b/a.py",
            "@@ -1,1 +1,3 @@",
            " existing",
            "+added_a",
            "+added_b",
            "diff --git a/b.py b/b.py",
            "--- a/b.py",
            "+++ b/b.py",
            "@@ -10,1 +10,2 @@",
            " existing",
            "+added_b",
            "diff --git a/image.png b/image.png",
            "Binary files a/image.png and b/image.png differ",
        ]
    )


def _comment(path: str, line: int | str, body: str = "Finding body") -> dict[str, object]:
    return {
        "path": path,
        "line": line,
        "side": "RIGHT",
        "body": body,
    }


def _body(fingerprint: str) -> str:
    return f"Finding body\n{INLINE_FINGERPRINT_PREFIX}{fingerprint}{INLINE_FINGERPRINT_SUFFIX}"


def test_valid_right_line_is_kept() -> None:
    payload = {"event": "COMMENT", "body": "full summary", "comments": [_comment("a.py", 2)]}

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")

    assert revalidated["comments"] == [_comment("a.py", 2)]


def test_unknown_path_is_dropped() -> None:
    payload = {"comments": [_comment("missing.py", 2)]}

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")

    assert revalidated["comments"] == []


def test_line_not_in_right_lines_is_dropped() -> None:
    payload = {"comments": [_comment("a.py", 99)]}

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")

    assert revalidated["comments"] == []


def test_binary_file_comment_is_dropped() -> None:
    payload = {"comments": [_comment("image.png", 1)]}

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")

    assert revalidated["comments"] == []


def test_empty_comments_stays_empty() -> None:
    payload = {"event": "COMMENT", "body": "full summary", "comments": []}

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")

    assert revalidated["comments"] == []


def test_output_body_is_minimal_and_commit_id_is_set() -> None:
    payload = {"event": "COMMENT", "body": "<!-- ai-code-review-qa:summary -->\n## Full Summary", "comments": []}

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")

    assert revalidated["event"] == "COMMENT"
    assert revalidated["commit_id"] == "abc123"
    assert revalidated["body"] == MINIMAL_REVIEW_BODY
    assert "ai-code-review-qa:summary" not in revalidated["body"]
    assert "Full Summary" not in revalidated["body"]


def test_revalidate_does_not_mutate_input_payload() -> None:
    payload = {"comments": [_comment("a.py", 2), _comment("missing.py", 2)]}
    original = copy.deepcopy(payload)

    revalidate_inline_payload(payload, _diff(), "abc123")

    assert payload == original


def test_cli_outputs_revalidated_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "inline-review.json"
    diff_path = tmp_path / "pr.diff"
    payload_path.write_text(json.dumps({"comments": [_comment("a.py", 2), _comment("a.py", 99)]}), encoding="utf-8")
    diff_path.write_text(_diff(), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(APP_DIR / "github_inline_revalidate.py"),
            "--payload",
            str(payload_path),
            "--diff",
            str(diff_path),
            "--commit-id",
            "abc123",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    revalidated = json.loads(completed.stdout)
    assert revalidated["commit_id"] == "abc123"
    assert revalidated["body"] == MINIMAL_REVIEW_BODY
    assert revalidated["comments"] == [_comment("a.py", 2)]


def test_importing_github_inline_revalidate_is_dependency_light() -> None:
    script = f"""
import json
import sys
sys.path.insert(0, {str(APP_DIR)!r})
for name in ["pydantic", "schemas", "backend.app.schemas", "github_review_reporter", "backend.app.github_review_reporter"]:
    sys.modules.pop(name, None)
import github_inline_revalidate  # noqa: F401
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


def test_revalidate_then_skip_existing_keeps_only_net_new_valid_comment() -> None:
    existing_fingerprint = "a" * 40
    new_fingerprint = "b" * 40
    payload = {
        "event": "COMMENT",
        "body": "full summary",
        "comments": [
            _comment("a.py", 2, _body(existing_fingerprint)),
            _comment("missing.py", 2, _body("c" * 40)),
            _comment("b.py", 11, _body(new_fingerprint)),
        ],
    }
    existing_comments = [{"body": _body(existing_fingerprint), "id": 1}]

    revalidated = revalidate_inline_payload(payload, _diff(), "abc123")
    filtered = select_new_inline_comments(existing_comments, revalidated)

    assert filtered["event"] == "COMMENT"
    assert filtered["commit_id"] == "abc123"
    assert filtered["body"] == MINIMAL_REVIEW_BODY
    assert filtered["comments"] == [_comment("b.py", 11, _body(new_fingerprint))]
