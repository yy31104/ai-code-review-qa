"""Enclosing function or class of each change, as extra context for the provider.

A diff shows a changed line with three lines of context on each side. A
reviewer usually needs more than that to judge the change: Bacchelli and Bird
("Expectations, Outcomes, and Challenges of Modern Code Review", ICSE 2013)
found that understanding the change and its context is the main thing
reviewers need and the main thing that makes review hard. This module gives the
provider the complete innermost function or class around each changed line,
taken from the file after the change.

Context is bounded and never decides coverage:

- Scopes come only from the diff text actually sent to the provider, so a file
  that the diff budget left out contributes no context, and a partially sent
  file contributes only scopes around the added lines that were sent. The
  ``incomplete`` status and its "not sent" list therefore mean exactly what
  they meant without context.
- Context has its own character budget. Whole scopes are sent while they fit;
  a scope that does not fit is listed as omitted, never cut in half.
- A source whose lines disagree with the diff's added lines (wrong revision,
  file edited since the diff was taken, a symlink) contributes nothing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from diff_index import DiffIndex

MAX_CONTEXT_CHARS = 8000

SourceLoader = Callable[[list[str]], Mapping[str, str]]

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class Scope:
    path: str
    kind: str
    name: str
    start: int
    end: int
    lines: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.path}:{self.start}-{self.end} {self.kind} {self.name}"

    def render(self) -> str:
        width = len(str(self.end))
        body = "".join(
            f"{number:>{width}} | {text}\n"
            for number, text in zip(range(self.start, self.end + 1), self.lines)
        )
        return f"# {self.label}\n{body}"


@dataclass(frozen=True)
class ChangeContext:
    scopes: tuple[Scope, ...] = ()
    #: Scopes that did not fit in the context budget.
    omitted: tuple[str, ...] = ()
    #: Files with changes whose source could not be used, with the reason.
    unavailable: tuple[str, ...] = ()
    limit: int = MAX_CONTEXT_CHARS

    @property
    def text(self) -> str:
        return "\n".join(scope.render() for scope in self.scopes)

    def detail(self) -> str | None:
        """Describe what context was left out. None when nothing was."""
        parts = []
        if self.omitted:
            parts.append(
                f"over the {self.limit}-character context budget: " + ", ".join(self.omitted)
            )
        if self.unavailable:
            parts.append("source unavailable: " + ", ".join(self.unavailable))
        if not parts:
            return None
        return "Enclosing-scope context was not sent for some changes; " + "; ".join(parts) + "."


@dataclass(frozen=True)
class _Node:
    kind: str
    name: str
    start: int
    end: int


def _scope_nodes(tree: ast.AST) -> list[_Node]:
    nodes: list[_Node] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPE_NODES):
                name = f"{prefix}{child.name}"
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                kind = {
                    ast.FunctionDef: "def",
                    ast.AsyncFunctionDef: "async def",
                    ast.ClassDef: "class",
                }[type(child)]
                nodes.append(_Node(kind, name, start, child.end_lineno or child.lineno))
                visit(child, f"{name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return nodes


def enclosing_scopes(source: str, added_lines: Iterable[int]) -> list[tuple[str, str, int, int]]:
    """Return ``(kind, name, start, end)`` for the innermost scope of each added line.

    Decorators belong to their scope. Lines outside every function and class
    have no enclosing scope. A scope that lies inside another selected scope is
    dropped, because the outer one's text already contains it. Raises
    ``SyntaxError`` when ``source`` does not parse.
    """
    nodes = _scope_nodes(ast.parse(source))
    chosen: set[_Node] = set()
    for line in added_lines:
        containing = [node for node in nodes if node.start <= line <= node.end]
        if containing:
            chosen.add(min(containing, key=lambda node: (node.end - node.start, -node.start)))

    outermost = [
        node
        for node in chosen
        if not any(
            other is not node and other.start <= node.start and node.end <= other.end
            for other in chosen
        )
    ]
    return [(node.kind, node.name, node.start, node.end) for node in sorted(outermost, key=lambda n: n.start)]


def build_change_context(
    sent_diff: DiffIndex,
    load_sources: SourceLoader | None,
    *,
    limit: int = MAX_CONTEXT_CHARS,
) -> ChangeContext:
    """Collect enclosing scopes for the added Python lines in ``sent_diff``."""
    if load_sources is None or limit <= 0:
        return ChangeContext(limit=limit)

    targets = [
        path
        for path, diff_file in sent_diff.files.items()
        if path.lower().endswith(".py") and not diff_file.is_binary and diff_file.right_lines
    ]
    if not targets:
        return ChangeContext(limit=limit)

    sources = load_sources(targets)
    scopes: list[Scope] = []
    omitted: list[str] = []
    unavailable: list[str] = []
    used = 0

    for path in targets:
        diff_file = sent_diff.files[path]
        source = sources.get(path)
        if source is None:
            unavailable.append(f"{path} (not readable)")
            continue
        source_lines = source.splitlines()
        if not _agrees_with_diff(source_lines, diff_file.right_source, diff_file.right_lines):
            unavailable.append(f"{path} (does not match the diff)")
            continue
        try:
            found = enclosing_scopes(source, sorted(diff_file.right_lines))
        except SyntaxError:
            unavailable.append(f"{path} (does not parse)")
            continue

        for kind, name, start, end in found:
            scope = Scope(path, kind, name, start, end, tuple(source_lines[start - 1 : end]))
            size = len(scope.render()) + (1 if scopes else 0)
            if used + size <= limit:
                scopes.append(scope)
                used += size
            else:
                omitted.append(scope.label)

    return ChangeContext(tuple(scopes), tuple(omitted), tuple(unavailable), limit)


def _agrees_with_diff(
    source_lines: list[str], right_source: Mapping[int, str], added: Iterable[int]
) -> bool:
    for line in added:
        if line > len(source_lines):
            return False
        if source_lines[line - 1].rstrip("\r") != right_source.get(line, "").rstrip("\r"):
            return False
    return True
