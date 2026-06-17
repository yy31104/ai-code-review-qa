from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX
except ImportError:  # pragma: no cover - package import fallback
    from .github_constants import INLINE_FINGERPRINT_PREFIX, INLINE_FINGERPRINT_SUFFIX


_FINGERPRINT_RE = re.compile(
    re.escape(INLINE_FINGERPRINT_PREFIX) + r"([0-9A-Fa-f]{40})" + re.escape(INLINE_FINGERPRINT_SUFFIX)
)


def extract_fingerprints(comment_body: str) -> set[str]:
    """Extract inline finding fingerprints from a GitHub comment body."""
    return {match.lower() for match in _FINGERPRINT_RE.findall(str(comment_body))}


def select_new_inline_comments(existing_comments: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of payload with already-posted inline comments removed."""
    existing_fingerprints: set[str] = set()
    for comment in existing_comments:
        existing_fingerprints.update(extract_fingerprints(str(comment.get("body", ""))))

    filtered_payload = {key: value for key, value in payload.items() if key != "comments"}
    filtered_comments: list[dict[str, Any]] = []
    for comment in payload.get("comments", []):
        if not isinstance(comment, dict):
            continue
        comment_fingerprints = extract_fingerprints(str(comment.get("body", "")))
        if comment_fingerprints & existing_fingerprints:
            continue
        filtered_comments.append(dict(comment))

    filtered_payload["comments"] = filtered_comments
    return filtered_payload


def main() -> int:
    args = _parse_args()
    existing_comments = json.loads(args.existing.read_text(encoding="utf-8-sig"))
    payload = json.loads(args.payload.read_text(encoding="utf-8-sig"))

    if not isinstance(existing_comments, list):
        raise ValueError("--existing must point to a JSON array of GitHub review comments")
    if not isinstance(payload, dict):
        raise ValueError("--payload must point to a JSON object")

    filtered_payload = select_new_inline_comments(existing_comments, payload)
    json.dump(filtered_payload, sys.stdout, indent=2, ensure_ascii=True)
    print()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter dry-run inline comments that already have fingerprints.")
    parser.add_argument("--existing", type=Path, required=True, help="Path to existing GitHub review comments JSON.")
    parser.add_argument("--payload", type=Path, required=True, help="Path to generated inline review payload JSON.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
