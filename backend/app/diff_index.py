from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass
class DiffFile:
    right_lines: set[int] = field(default_factory=set)
    left_lines: set[int] = field(default_factory=set)
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

    for raw_line in diff.splitlines():
        line = raw_line.rstrip("\r")

        diff_match = _DIFF_HEADER_RE.match(line)
        if diff_match:
            pending_old_path = _normalize_path(diff_match.group(1))
            pending_new_path = _normalize_path(diff_match.group(2))
            pending_is_rename = False
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
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(2))
            continue

        if current is None or old_line is None or new_line is None:
            continue

        if line.startswith("\\"):
            continue
        if line.startswith(" "):
            old_line += 1
            new_line += 1
            continue
        if line.startswith("+"):
            current.right_lines.add(new_line)
            new_line += 1
            continue
        if line.startswith("-"):
            current.left_lines.add(old_line)
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


def _normalize_path(path: str | None) -> str:
    if not path:
        return ""
    return path.strip().strip('"').replace("\\", "/")
