from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX  # noqa: E402
from github_inline_upsert import extract_fingerprints, select_new_inline_comments  # noqa: E402


FINGERPRINT_ONE = "a" * 40
FINGERPRINT_TWO = "b" * 40


def _body(fingerprint: str) -> str:
    return f"Finding body\n{INLINE_FINGERPRINT_PREFIX}{fingerprint}{INLINE_FINGERPRINT_SUFFIX}"


def test_extract_fingerprints() -> None:
    body = "\n".join(
        [
            _body(FINGERPRINT_ONE),
            "ordinary text",
            _body(FINGERPRINT_TWO.upper()),
        ]
    )

    assert extract_fingerprints(body) == {FINGERPRINT_ONE, FINGERPRINT_TWO}


def test_select_new_inline_comments_skips_existing_fingerprints() -> None:
    payload = {
        "event": "COMMENT",
        "commit_id": "abc123",
        "body": "summary",
        "comments": [
            {"path": "a.py", "line": 2, "side": "RIGHT", "body": _body(FINGERPRINT_ONE)},
            {"path": "b.py", "line": 5, "side": "RIGHT", "body": _body(FINGERPRINT_TWO)},
        ],
    }
    existing = [{"body": _body(FINGERPRINT_ONE), "id": 1}]

    filtered = select_new_inline_comments(existing, payload)

    assert filtered["event"] == "COMMENT"
    assert filtered["commit_id"] == "abc123"
    assert filtered["body"] == "summary"
    assert filtered["comments"] == [payload["comments"][1]]


def test_select_new_inline_comments_keeps_comments_without_existing_fingerprint() -> None:
    payload = {
        "event": "COMMENT",
        "body": "summary",
        "comments": [
            {"path": "a.py", "line": 2, "side": "RIGHT", "body": _body(FINGERPRINT_ONE)},
            {"path": "b.py", "line": 5, "side": "RIGHT", "body": "No marker yet."},
        ],
    }

    filtered = select_new_inline_comments([], payload)

    assert filtered["comments"] == payload["comments"]


def test_select_new_inline_comments_does_not_mutate_input_payload() -> None:
    payload = {
        "event": "COMMENT",
        "body": "summary",
        "comments": [{"path": "a.py", "line": 2, "side": "RIGHT", "body": _body(FINGERPRINT_ONE)}],
    }
    original = copy.deepcopy(payload)

    select_new_inline_comments([{"body": _body(FINGERPRINT_ONE)}], payload)

    assert payload == original


def test_cli_filters_payload_to_stdout(tmp_path: Path) -> None:
    existing_path = tmp_path / "existing.json"
    payload_path = tmp_path / "payload.json"
    existing_path.write_text(json.dumps([{"body": _body(FINGERPRINT_ONE)}]), encoding="utf-8")
    payload_path.write_text(
        json.dumps(
            {
                "event": "COMMENT",
                "body": "summary",
                "comments": [
                    {"path": "a.py", "line": 2, "side": "RIGHT", "body": _body(FINGERPRINT_ONE)},
                    {"path": "b.py", "line": 5, "side": "RIGHT", "body": _body(FINGERPRINT_TWO)},
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(APP_DIR / "github_inline_upsert.py"),
            "--existing",
            str(existing_path),
            "--payload",
            str(payload_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    filtered = json.loads(completed.stdout)
    assert filtered["comments"][0]["body"] == _body(FINGERPRINT_TWO)


def test_github_inline_upsert_import_is_stdlib_only() -> None:
    script = f"""
import json
import sys
sys.path.insert(0, {str(APP_DIR)!r})
for name in ["pydantic", "schemas", "backend.app.schemas", "github_review_reporter", "backend.app.github_review_reporter"]:
    sys.modules.pop(name, None)
import github_inline_upsert  # noqa: F401
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
