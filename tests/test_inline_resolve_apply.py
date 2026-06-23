from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import evals.run_local  # noqa: F401
from github_inline_resolve_apply import select_resolvable_threads


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"


def _action(thread_node_id: object, *, eligible: bool = True, reason: str = "eligible") -> dict[str, object]:
    return {
        "comment_id": 101,
        "thread_node_id": thread_node_id,
        "eligible": eligible,
        "reason": reason,
    }


def test_eligible_only_ids_are_selected() -> None:
    selected = select_resolvable_threads(
        {
            "actions": [
                _action("thread-1"),
                _action("thread-2", eligible=False, reason="not_bot_author"),
            ]
        }
    )

    assert selected == {"thread_node_ids": ["thread-1"], "count": 1}


def test_all_ineligible_reasons_are_excluded() -> None:
    plan = {
        "actions": [
            _action("thread-1", eligible=False, reason="thread_not_found"),
            _action("thread-2", eligible=False, reason="not_bot_author"),
            _action("thread-3", eligible=False, reason="no_marker"),
            _action("thread-4", eligible=False, reason="already_resolved"),
        ]
    }

    assert select_resolvable_threads(plan) == {"thread_node_ids": [], "count": 0}


def test_duplicate_thread_node_id_is_deduped_first_seen() -> None:
    selected = select_resolvable_threads(
        {
            "actions": [
                _action("thread-2"),
                _action("thread-1"),
                _action("thread-2"),
            ]
        }
    )

    assert selected == {"thread_node_ids": ["thread-2", "thread-1"], "count": 2}


def test_none_empty_and_non_string_thread_node_ids_are_skipped() -> None:
    selected = select_resolvable_threads(
        {
            "actions": [
                _action(None),
                _action(""),
                _action(123),
                _action("thread-1"),
            ]
        }
    )

    assert selected == {"thread_node_ids": ["thread-1"], "count": 1}


def test_empty_plan_returns_empty_selection() -> None:
    assert select_resolvable_threads({}) == {"thread_node_ids": [], "count": 0}
    assert select_resolvable_threads({"actions": "not-a-list"}) == {"thread_node_ids": [], "count": 0}


def test_select_resolvable_threads_does_not_mutate_input() -> None:
    plan = {"actions": [_action("thread-1"), _action("thread-1")]}
    original = copy.deepcopy(plan)

    select_resolvable_threads(plan)

    assert plan == original


def test_cli_prints_resolve_apply_json(tmp_path: Path) -> None:
    plan_path = tmp_path / "stale-action-plan.json"
    plan_path.write_text(json.dumps({"actions": [_action("thread-1")]}), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(APP_DIR / "github_inline_resolve_apply.py"),
            "--plan",
            str(plan_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )

    assert json.loads(completed.stdout) == {"thread_node_ids": ["thread-1"], "count": 1}


def test_module_contains_no_network_or_process_calls() -> None:
    source = (APP_DIR / "github_inline_resolve_apply.py").read_text(encoding="utf-8")

    assert "requests" not in source
    assert "urllib" not in source
    assert "http.client" not in source
    assert "socket" not in source
    assert "subprocess" not in source
    assert "gh api" not in source
    assert "pydantic" not in source
    assert "schemas" not in source


def test_github_inline_resolve_apply_import_is_stdlib_only() -> None:
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
import github_inline_resolve_apply  # noqa: F401
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
