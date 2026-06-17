from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from github_constants import SUMMARY_MARKER
except ImportError:  # pragma: no cover - package import fallback
    from .github_constants import SUMMARY_MARKER


def decide_comment_action(
    existing_comments: list[dict[str, Any]],
    *,
    marker: str = SUMMARY_MARKER,
    bot_login: str = "github-actions[bot]",
) -> tuple[str, int | None]:
    """Choose whether to create or patch a marker-based summary comment."""
    matches = [
        (index, comment)
        for index, comment in enumerate(existing_comments)
        if marker in str(comment.get("body", ""))
    ]
    if not matches:
        return ("create", None)

    if len(matches) == 1:
        return ("patch", _comment_id(matches[0][1]))

    duplicate_ids = [_comment_id(comment) for _, comment in matches]
    bot_matches = [
        (index, comment)
        for index, comment in matches
        if _comment_author_login(comment) == bot_login
    ]
    preferred_matches = bot_matches or matches
    _, selected = min(preferred_matches, key=_oldest_sort_key)
    selected_id = _comment_id(selected)
    print(
        "Warning: multiple summary comments contain marker; "
        f"patching {selected_id}. Duplicate IDs: {', '.join(str(comment_id) for comment_id in duplicate_ids)}",
        file=sys.stderr,
    )
    return ("patch", selected_id)


def main() -> int:
    args = _parse_args()
    comments = json.loads(args.comments.read_text(encoding="utf-8-sig"))
    if not isinstance(comments, list):
        raise ValueError("--comments must point to a JSON array of GitHub comments")

    action, comment_id = decide_comment_action(comments)
    if action == "create":
        print("create")
    else:
        print(f"patch {comment_id}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decide marker-based GitHub summary comment upsert action.")
    parser.add_argument("--comments", type=Path, required=True, help="Path to GitHub issue comments JSON.")
    return parser.parse_args()


def _comment_id(comment: dict[str, Any]) -> int:
    return int(comment.get("id", 0))


def _comment_author_login(comment: dict[str, Any]) -> str:
    user = comment.get("user")
    if isinstance(user, dict):
        return str(user.get("login", ""))
    return ""


def _oldest_sort_key(match: tuple[int, dict[str, Any]]) -> tuple[str, int]:
    index, comment = match
    created_at = str(comment.get("created_at") or "")
    return (created_at, index)


if __name__ == "__main__":
    raise SystemExit(main())
