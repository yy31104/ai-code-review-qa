from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from github_inline_upsert import extract_fingerprints
except ImportError:  # pragma: no cover - package import fallback
    from .github_inline_upsert import extract_fingerprints


def build_stale_plan(existing_comments: list[dict[str, Any]], current_fingerprints: set[str] | list[str]) -> dict[str, Any]:
    """Build a read-only plan for marker-owned inline comments no longer in the current findings."""
    current = {str(fingerprint).lower() for fingerprint in current_fingerprints}
    stale: list[dict[str, Any]] = []
    considered_count = 0

    for comment in existing_comments:
        fingerprints = extract_fingerprints(str(comment.get("body", "")))
        if not fingerprints:
            continue

        considered_count += 1
        for fingerprint in sorted(fingerprints):
            if fingerprint in current:
                continue
            stale.append(
                {
                    "comment_id": comment.get("id"),
                    "fingerprint": fingerprint,
                    "path": comment.get("path"),
                    "line": comment.get("line"),
                    "author": _comment_author(comment),
                }
            )

    return {
        "stale": stale,
        "stale_count": len(stale),
        "considered_count": considered_count,
    }


def main() -> int:
    args = _parse_args()
    existing_comments = json.loads(args.existing.read_text(encoding="utf-8-sig"))
    fingerprints_payload = json.loads(args.fingerprints.read_text(encoding="utf-8-sig"))

    if not isinstance(existing_comments, list):
        raise ValueError("--existing must point to a JSON array of GitHub review comments")
    if not isinstance(fingerprints_payload, dict):
        raise ValueError("--fingerprints must point to a JSON object")

    current_fingerprints = fingerprints_payload.get("fingerprints", [])
    if not isinstance(current_fingerprints, list):
        raise ValueError("--fingerprints JSON must contain a fingerprints array")

    plan = build_stale_plan(existing_comments, current_fingerprints)
    json.dump(plan, sys.stdout, indent=2, ensure_ascii=True)
    print()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a dry-run stale inline comment plan.")
    parser.add_argument("--existing", type=Path, required=True, help="Path to existing GitHub review comments JSON.")
    parser.add_argument("--fingerprints", type=Path, required=True, help="Path to finding-fingerprints.json.")
    return parser.parse_args()


def _comment_author(comment: dict[str, Any]) -> str:
    user = comment.get("user")
    if isinstance(user, dict):
        return str(user.get("login", ""))
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
