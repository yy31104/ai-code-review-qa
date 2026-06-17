from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from diff_index import DiffIndex, parse_unified_diff
except ImportError:  # pragma: no cover - package import fallback
    from .diff_index import DiffIndex, parse_unified_diff


MINIMAL_REVIEW_BODY = "AI Code Review inline findings. See the summary comment for the full report."


def revalidate_inline_payload(payload: dict[str, Any], diff_text: str, commit_id: str) -> dict[str, Any]:
    """Return a create-review payload containing only comments valid for this diff."""
    diff_index = parse_unified_diff(diff_text)
    comments = [
        dict(comment)
        for comment in payload.get("comments", [])
        if isinstance(comment, dict) and _is_valid_inline_comment(diff_index, comment)
    ]
    return {
        "event": "COMMENT",
        "commit_id": commit_id,
        "body": MINIMAL_REVIEW_BODY,
        "comments": comments,
    }


def main() -> int:
    args = _parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("--payload must point to a JSON object")

    diff_text = args.diff.read_text(encoding="utf-8", errors="replace")
    revalidated = revalidate_inline_payload(payload, diff_text, args.commit_id)
    json.dump(revalidated, sys.stdout, indent=2, ensure_ascii=True)
    print()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Revalidate inline review payload comments against a PR diff.")
    parser.add_argument("--payload", type=Path, required=True, help="Path to inline-review.json.")
    parser.add_argument("--diff", type=Path, required=True, help="Path to the unified PR diff.")
    parser.add_argument("--commit-id", required=True, help="PR head SHA to include in the create-review payload.")
    return parser.parse_args()


def _is_valid_inline_comment(diff_index: DiffIndex, comment: dict[str, Any]) -> bool:
    path = _normalize_path(str(comment.get("path", "")))
    if not path:
        return False

    line = _line_number(comment.get("line"))
    if line is None:
        return False

    diff_file = diff_index.files.get(path)
    if diff_file is None or diff_file.is_binary:
        return False

    return line in diff_file.right_lines


def _line_number(value: Any) -> int | None:
    try:
        line = int(value)
    except (TypeError, ValueError):
        return None
    if line <= 0:
        return None
    return line


def _normalize_path(path: str) -> str:
    return path.strip().strip('"').replace("\\", "/")


if __name__ == "__main__":
    raise SystemExit(main())
