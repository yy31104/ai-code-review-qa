from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class ChangeBlock:
    """One run of removed and added lines, unseparated by context.

    Git does not mark which removed line a given added line replaced, but a
    modification always appears as adjacent `-` and `+` lines inside one block.
    That adjacency is what lets a rule ask whether this diff *introduced* the
    construct it matched, or merely edited a line that already contained it.
    """

    removed: dict[int, str] = field(default_factory=dict)
    added: dict[int, str] = field(default_factory=dict)


@dataclass
class DiffFile:
    right_lines: set[int] = field(default_factory=set)
    left_lines: set[int] = field(default_factory=set)
    # Right-side source text keyed by post-change line number. Added lines and
    # unchanged context lines are both recorded, because a rule often needs the
    # surrounding context to read an added line correctly. Only the line
    # numbers in ``right_lines`` were actually added by this diff.
    right_source: dict[int, str] = field(default_factory=dict)
    #: Left-side source text keyed by pre-change line number, for removed lines.
    left_source: dict[int, str] = field(default_factory=dict)
    #: Runs of adjacent removed/added lines, in diff order.
    change_blocks: list[ChangeBlock] = field(default_factory=list)
    #: Added line number -> index into ``change_blocks``.
    block_of_added: dict[int, int] = field(default_factory=dict)
    is_binary: bool = False
    is_rename: bool = False
    new_path: str | None = None


@dataclass
class DiffIndex:
    files: dict[str, DiffFile] = field(default_factory=dict)


def parse_unified_diff(diff: str) -> DiffIndex:
    index = DiffIndex()
    pending_old_path: str | None = None
    pending_new_path: str | None = None
    pending_is_rename = False
    current: DiffFile | None = None
    old_line: int | None = None
    new_line: int | None = None
    open_block: ChangeBlock | None = None

    def close_block() -> None:
        nonlocal open_block
        open_block = None

    def ensure_block(diff_file: DiffFile) -> ChangeBlock:
        nonlocal open_block
        if open_block is None:
            open_block = ChangeBlock()
            diff_file.change_blocks.append(open_block)
        return open_block

    for raw_line in diff.splitlines():
        line = raw_line.rstrip("\r")

        diff_match = _DIFF_HEADER_RE.match(line)
        if diff_match:
            pending_old_path = _normalize_path(diff_match.group(1))
            pending_new_path = _normalize_path(diff_match.group(2))
            pending_is_rename = False
            close_block()
            current = None
            old_line = None
            new_line = None
            continue

        if line.startswith("rename from "):
            pending_old_path = _normalize_path(line.removeprefix("rename from ").strip())
            pending_is_rename = True
            continue

        if line.startswith("rename to "):
            pending_new_path = _normalize_path(line.removeprefix("rename to ").strip())
            pending_is_rename = True
            if pending_new_path:
                current = _ensure_file(index, pending_new_path, pending_new_path, pending_is_rename)
            continue

        if line.startswith("Binary files ") or line == "GIT binary patch":
            close_block()
            path = pending_new_path or pending_old_path
            if path:
                current = _ensure_file(index, path, pending_new_path, pending_is_rename)
                current.is_binary = True
            old_line = None
            new_line = None
            continue

        if line.startswith("--- "):
            old_path = line[4:].strip()
            if old_path == "/dev/null":
                pending_old_path = None
            else:
                pending_old_path = _normalize_prefixed_path(old_path)
            close_block()
            current = None
            old_line = None
            new_line = None
            continue

        if line.startswith("+++ "):
            new_path = line[4:].strip()
            if new_path == "/dev/null":
                pending_new_path = None
                key = pending_old_path
            else:
                pending_new_path = _normalize_prefixed_path(new_path)
                key = pending_new_path

            if key:
                current = _ensure_file(index, key, pending_new_path, pending_is_rename)
            else:
                current = None
            old_line = None
            new_line = None
            continue

        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match:
            close_block()
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(2))
            continue

        if current is None or old_line is None or new_line is None:
            continue

        if line.startswith("\\"):
            continue
        if line.startswith(" "):
            close_block()
            current.right_source[new_line] = line[1:]
            old_line += 1
            new_line += 1
            continue
        if line.startswith("+"):
            block = ensure_block(current)
            block.added[new_line] = line[1:]
            current.block_of_added[new_line] = len(current.change_blocks) - 1
            current.right_lines.add(new_line)
            current.right_source[new_line] = line[1:]
            new_line += 1
            continue
        if line.startswith("-"):
            block = ensure_block(current)
            block.removed[old_line] = line[1:]
            current.left_lines.add(old_line)
            current.left_source[old_line] = line[1:]
            old_line += 1

    return index


def resolve_anchor(diff_index: DiffIndex, file: str | None) -> Optional[int]:
    if file is None:
        return None

    diff_file = diff_index.files.get(_normalize_path(file))
    if diff_file is None or diff_file.is_binary or not diff_file.right_lines:
        return None
    return min(diff_file.right_lines)


def _ensure_file(index: DiffIndex, key: str, new_path: str | None, is_rename: bool) -> DiffFile:
    normalized_key = _normalize_path(key)
    diff_file = index.files.setdefault(normalized_key, DiffFile())
    diff_file.new_path = _normalize_path(new_path) if new_path else None
    diff_file.is_rename = diff_file.is_rename or is_rename
    return diff_file


def _normalize_prefixed_path(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return _normalize_path(path[2:])
    return _normalize_path(path)


def normalize_path(path: str | None) -> str:
    """Normalize a diff path so lookups agree across platforms and quoting."""
    if not path:
        return ""
    return path.strip().strip('"').replace("\\", "/")


# Kept as the internal spelling used throughout this module.
_normalize_path = normalize_path
