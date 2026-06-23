from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import evals.run_local  # noqa: F401
from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX
from github_inline_resolve_plan import build_resolve_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"

FINGERPRINT_ONE = "a" * 40
FINGERPRINT_TWO = "b" * 40


def _body(fingerprint: str = FINGERPRINT_ONE) -> str:
    return f"Finding body\n{INLINE_FINGERPRINT_PREFIX}{fingerprint}{INLINE_FINGERPRINT_SUFFIX}"


def _stale_plan(*comment_ids: int) -> dict[str, object]:
    return {
        "stale": [
            {
                "comment_id": comment_id,
                "fingerprint": FINGERPRINT_ONE,
                "path": "a.py",
                "line": 2,
                "author": "github-actions[bot]",
            }
            for comment_id in comment_ids
        ],
        "stale_count": len(comment_ids),
        "considered_count": len(comment_ids),
    }


def _graphql_response(*threads: dict[str, object]) -> dict[str, object]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": list(threads),
                    }
                }
            }
        }
    }


def _thread(
    thread_id: str,
    comment_id: int,
    *,
    author: str = "github-actions[bot]",
    body: str | None = None,
    is_resolved: bool = False,
    is_outdated: bool = False,
) -> dict[str, object]:
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": is_outdated,
        "comments": {
            "nodes": [
                {
                    "databaseId": comment_id,
                    "author": {"login": author},
                    "body": _body() if body is None else body,
                }
            ]
        },
    }


def test_marker_bot_thread_found_unresolved_is_eligible() -> None:
    plan = build_resolve_plan(_stale_plan(101), _graphql_response(_thread("thread-1", 101)))

    assert plan == {
        "actions": [
            {
                "comment_id": 101,
                "thread_node_id": "thread-1",
                "is_resolved": False,
                "author": "github-actions[bot]",
                "marker_present": True,
                "eligible": True,
                "reason": "eligible",
            }
        ],
        "eligible_count": 1,
        "total_stale": 1,
    }


def test_human_author_with_marker_is_not_eligible() -> None:
    plan = build_resolve_plan(_stale_plan(101), _graphql_response(_thread("thread-1", 101, author="alice")))

    assert plan["actions"][0]["eligible"] is False
    assert plan["actions"][0]["reason"] == "not_bot_author"
    assert plan["actions"][0]["marker_present"] is True


def test_other_bot_author_with_marker_is_not_eligible() -> None:
    plan = build_resolve_plan(
        _stale_plan(101),
        _graphql_response(_thread("thread-1", 101, author="dependabot[bot]")),
    )

    assert plan["actions"][0]["eligible"] is False
    assert plan["actions"][0]["reason"] == "not_bot_author"


def test_bot_author_without_marker_is_not_eligible() -> None:
    plan = build_resolve_plan(
        _stale_plan(101),
        _graphql_response(_thread("thread-1", 101, body="ordinary review comment")),
    )

    assert plan["actions"][0]["eligible"] is False
    assert plan["actions"][0]["reason"] == "no_marker"
    assert plan["actions"][0]["marker_present"] is False


def test_missing_review_thread_is_not_eligible() -> None:
    plan = build_resolve_plan(_stale_plan(101), _graphql_response(_thread("thread-1", 202)))

    assert plan["actions"][0] == {
        "comment_id": 101,
        "thread_node_id": None,
        "is_resolved": None,
        "author": "",
        "marker_present": False,
        "eligible": False,
        "reason": "thread_not_found",
    }


def test_already_resolved_thread_is_not_eligible() -> None:
    plan = build_resolve_plan(
        _stale_plan(101),
        _graphql_response(_thread("thread-1", 101, is_resolved=True)),
    )

    assert plan["actions"][0]["eligible"] is False
    assert plan["actions"][0]["reason"] == "already_resolved"


def test_duplicate_stale_entries_and_multiple_threads_are_deterministic() -> None:
    stale_plan = _stale_plan(202, 101, 202)
    response = _graphql_response(
        _thread("thread-1", 101, body=_body(FINGERPRINT_ONE)),
        _thread("thread-2", 202, body=_body(FINGERPRINT_TWO)),
    )

    plan = build_resolve_plan(stale_plan, response)

    assert [action["comment_id"] for action in plan["actions"]] == [202, 101, 202]
    assert [action["thread_node_id"] for action in plan["actions"]] == ["thread-2", "thread-1", "thread-2"]
    assert plan["eligible_count"] == 3
    assert plan["total_stale"] == 3


def test_slurped_paginated_review_threads_merge_nodes_in_order() -> None:
    response = [
        _graphql_response(_thread("thread-1", 101, body=_body(FINGERPRINT_ONE))),
        _graphql_response(_thread("thread-2", 202, body=_body(FINGERPRINT_TWO))),
    ]

    plan = build_resolve_plan(_stale_plan(101, 202), response)

    assert [action["thread_node_id"] for action in plan["actions"]] == ["thread-1", "thread-2"]
    assert [action["reason"] for action in plan["actions"]] == ["eligible", "eligible"]
    assert plan["eligible_count"] == 2


def test_single_graphql_object_input_still_works() -> None:
    plan = build_resolve_plan(_stale_plan(101), _graphql_response(_thread("thread-1", 101)))

    assert plan["actions"][0]["thread_node_id"] == "thread-1"
    assert plan["actions"][0]["reason"] == "eligible"


def test_empty_and_malformed_slurped_pages_are_safe() -> None:
    response = [
        {},
        {"data": None},
        {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": "not-a-list"}}}}},
        ["not-a-page-object"],
    ]

    plan = build_resolve_plan(_stale_plan(101), response)

    assert plan == {
        "actions": [
            {
                "comment_id": 101,
                "thread_node_id": None,
                "is_resolved": None,
                "author": "",
                "marker_present": False,
                "eligible": False,
                "reason": "thread_not_found",
            }
        ],
        "eligible_count": 0,
        "total_stale": 1,
    }


def test_parses_nested_review_threads_comments_nodes() -> None:
    response = _graphql_response(
        {
            "id": "thread-1",
            "isResolved": False,
            "isOutdated": True,
            "comments": {
                "nodes": [
                    {
                        "databaseId": 101,
                        "author": {"login": "github-actions[bot]"},
                        "body": _body(),
                    },
                    {
                        "databaseId": 102,
                        "author": {"login": "alice"},
                        "body": _body(FINGERPRINT_TWO),
                    },
                ]
            },
        }
    )

    plan = build_resolve_plan(_stale_plan(102, 101), response)

    assert [action["reason"] for action in plan["actions"]] == ["not_bot_author", "eligible"]


def test_empty_and_missing_fields_are_safe() -> None:
    assert build_resolve_plan({}, {}) == {"actions": [], "eligible_count": 0, "total_stale": 0}
    assert build_resolve_plan({"stale": [{"comment_id": 101}]}, {"data": {}})["actions"][0]["reason"] == (
        "thread_not_found"
    )
    response = _graphql_response({"id": "thread-1", "comments": {"nodes": [{"databaseId": 101}]}})

    plan = build_resolve_plan(_stale_plan(101), response)

    assert plan["actions"][0]["reason"] == "not_bot_author"
    assert plan["actions"][0]["author"] == ""


def test_build_resolve_plan_does_not_mutate_input() -> None:
    stale_plan = _stale_plan(101)
    response = _graphql_response(_thread("thread-1", 101))
    original_stale = copy.deepcopy(stale_plan)
    original_response = copy.deepcopy(response)

    build_resolve_plan(stale_plan, response)

    assert stale_plan == original_stale
    assert response == original_response


def test_module_contains_no_network_or_process_calls() -> None:
    source = (APP_DIR / "github_inline_resolve_plan.py").read_text(encoding="utf-8")

    assert "requests" not in source
    assert "urllib" not in source
    assert "http.client" not in source
    assert "socket" not in source
    assert "subprocess" not in source
    assert "gh api" not in source


def test_cli_prints_stale_action_plan(tmp_path: Path) -> None:
    stale_path = tmp_path / "stale-plan.json"
    threads_path = tmp_path / "review-threads.json"
    stale_path.write_text(json.dumps(_stale_plan(101)), encoding="utf-8")
    threads_path.write_text(json.dumps(_graphql_response(_thread("thread-1", 101))), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(APP_DIR / "github_inline_resolve_plan.py"),
            "--stale",
            str(stale_path),
            "--threads",
            str(threads_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    plan = json.loads(completed.stdout)
    assert plan["eligible_count"] == 1
    assert plan["actions"][0]["thread_node_id"] == "thread-1"


def test_github_inline_resolve_plan_import_is_stdlib_only() -> None:
    script = f"""
import json
import sys
sys.path.insert(0, {str(APP_DIR)!r})
for name in [
    "pydantic",
    "schemas",
    "backend.app.schemas",
    "github_review_reporter",
    "backend.app.github_review_reporter",
    "diff_index",
    "backend.app.diff_index",
]:
    sys.modules.pop(name, None)
import github_inline_resolve_plan  # noqa: F401
print(json.dumps({{
    name: name in sys.modules
    for name in [
        "pydantic",
        "schemas",
        "backend.app.schemas",
        "github_review_reporter",
        "backend.app.github_review_reporter",
        "diff_index",
        "backend.app.diff_index",
    ]
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
        "diff_index": False,
        "backend.app.diff_index": False,
    }
