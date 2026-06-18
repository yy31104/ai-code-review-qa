import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from github_inline_upsert import extract_fingerprints
except ImportError:  # pragma: no cover - package import fallback
    from .github_inline_upsert import extract_fingerprints


BOT_AUTHOR = "github-actions[bot]"


def build_resolve_plan(stale_plan: dict[str, Any], graphql_response: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only future resolve plan for stale marker-owned bot comments."""
    comment_index = _index_review_thread_comments(graphql_response)
    stale_entries = stale_plan.get("stale", [])
    if not isinstance(stale_entries, list):
        stale_entries = []

    actions: list[dict[str, Any]] = []
    for stale_entry in stale_entries:
        if not isinstance(stale_entry, dict):
            continue

        comment_id = stale_entry.get("comment_id")
        matched = comment_index.get(_comment_key(comment_id))
        action = _action_for_stale_entry(comment_id, matched)
        actions.append(action)

    eligible_count = sum(1 for action in actions if action["eligible"])
    return {
        "actions": actions,
        "eligible_count": eligible_count,
        "total_stale": len(actions),
    }


def main() -> int:
    args = _parse_args()
    stale_plan = json.loads(args.stale.read_text(encoding="utf-8-sig"))
    graphql_response = json.loads(args.threads.read_text(encoding="utf-8-sig"))

    if not isinstance(stale_plan, dict):
        raise ValueError("--stale must point to a JSON object")
    if not isinstance(graphql_response, dict):
        raise ValueError("--threads must point to a JSON object")

    plan = build_resolve_plan(stale_plan, graphql_response)
    json.dump(plan, sys.stdout, indent=2, ensure_ascii=True)
    print()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a dry-run stale inline resolve action plan.")
    parser.add_argument("--stale", type=Path, required=True, help="Path to stale-plan.json.")
    parser.add_argument("--threads", type=Path, required=True, help="Path to review-threads.json from GraphQL.")
    return parser.parse_args()


def _index_review_thread_comments(graphql_response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for thread in _review_thread_nodes(graphql_response):
        if not isinstance(thread, dict):
            continue

        comments = thread.get("comments")
        comment_nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
        if not isinstance(comment_nodes, list):
            continue

        for comment in comment_nodes:
            if not isinstance(comment, dict):
                continue

            key = _comment_key(comment.get("databaseId"))
            if not key:
                continue
            if key in index:
                continue

            author = comment.get("author")
            author_login = author.get("login", "") if isinstance(author, dict) else ""
            index[key] = {
                "thread_node_id": thread.get("id"),
                "is_resolved": bool(thread.get("isResolved", False)),
                "author_login": str(author_login),
                "body": str(comment.get("body", "")),
            }

    return index


def _review_thread_nodes(graphql_response: dict[str, Any]) -> list[Any]:
    data = graphql_response.get("data", graphql_response)
    if not isinstance(data, dict):
        return []

    repository = data.get("repository")
    if not isinstance(repository, dict):
        return []

    pull_request = repository.get("pullRequest")
    if not isinstance(pull_request, dict):
        return []

    review_threads = pull_request.get("reviewThreads")
    if not isinstance(review_threads, dict):
        return []

    nodes = review_threads.get("nodes", [])
    return nodes if isinstance(nodes, list) else []


def _action_for_stale_entry(comment_id: Any, matched: dict[str, Any] | None) -> dict[str, Any]:
    if matched is None:
        return {
            "comment_id": comment_id,
            "thread_node_id": None,
            "is_resolved": None,
            "author": "",
            "marker_present": False,
            "eligible": False,
            "reason": "thread_not_found",
        }

    marker_present = bool(extract_fingerprints(matched["body"]))
    author = matched["author_login"]
    is_resolved = matched["is_resolved"]

    if author != BOT_AUTHOR:
        eligible = False
        reason = "not_bot_author"
    elif not marker_present:
        eligible = False
        reason = "no_marker"
    elif is_resolved:
        eligible = False
        reason = "already_resolved"
    else:
        eligible = True
        reason = "eligible"

    return {
        "comment_id": comment_id,
        "thread_node_id": matched["thread_node_id"],
        "is_resolved": is_resolved,
        "author": author,
        "marker_present": marker_present,
        "eligible": eligible,
        "reason": reason,
    }


def _comment_key(comment_id: Any) -> str:
    if comment_id is None:
        return ""
    return str(comment_id)


if __name__ == "__main__":
    raise SystemExit(main())
