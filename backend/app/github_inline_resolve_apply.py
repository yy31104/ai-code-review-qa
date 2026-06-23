import argparse
import json
import sys
from pathlib import Path
from typing import Any


def select_resolvable_threads(resolve_plan: dict[str, Any]) -> dict[str, Any]:
    """Select eligible review thread node IDs from a stale resolve action plan."""
    actions = resolve_plan.get("actions", [])
    if not isinstance(actions, list):
        actions = []

    seen: set[str] = set()
    thread_node_ids: list[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("eligible") is not True:
            continue

        thread_node_id = action.get("thread_node_id")
        if not isinstance(thread_node_id, str) or not thread_node_id:
            continue
        if thread_node_id in seen:
            continue

        seen.add(thread_node_id)
        thread_node_ids.append(thread_node_id)

    return {
        "thread_node_ids": thread_node_ids,
        "count": len(thread_node_ids),
    }


def main() -> int:
    args = _parse_args()
    resolve_plan = json.loads(args.plan.read_text(encoding="utf-8-sig"))

    if not isinstance(resolve_plan, dict):
        raise ValueError("--plan must point to a JSON object")

    selected = select_resolvable_threads(resolve_plan)
    json.dump(selected, sys.stdout, indent=2, ensure_ascii=True)
    print()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select stale review threads that may be resolved.")
    parser.add_argument("--plan", type=Path, required=True, help="Path to stale-action-plan.json.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
