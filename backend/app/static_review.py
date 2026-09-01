"""Deterministic review rules that read the added lines of a unified diff.

Every rule here inspects real source text and anchors its result to the exact
added line it matched, together with that line as evidence. No rule calls a
model, so the output is reproducible and can be used as the baseline that model
output is compared against.

Scope and limits, stated up front because they bound what a match means:

- Only right-side lines are inspected. Unchanged context lines are read for
  context but never reported; deleted lines are ignored entirely.
- Matching is lexical. There is no import resolution, type information, or
  cross-function data flow, so a match means "this shape is usually a defect",
  not "this line is proven wrong". Severity and confidence say how strongly.
- Continuation lines are joined into one logical line while brackets are open,
  so a call split across lines is matched as a whole. Triple-quoted strings
  spanning lines are not tracked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from diff_index import ChangeBlock, DiffFile, parse_unified_diff

MAX_EVIDENCE_CHARS = 200


@dataclass(frozen=True)
class LogicalLine:
    """One statement of right-side source, anchored at its first added line."""

    path: str
    line: int
    #: Source with comments removed but string contents intact.
    code: str
    #: The same source with the inside of every string literal emptied. Rules
    #: that must not fire on text merely mentioned in a string (``shell=True``
    #: inside a log message, an ``assert`` inside a docstring) match on this.
    code_outside_strings: str
    #: The same statement restricted to the lines this diff actually added. A
    #: rule matches on this when it must not fire on a construct that was
    #: already there and merely sits in the joined statement as context.
    added_code: str
    added_code_outside_strings: str
    raw: str
    following: tuple[str, ...] = ()
    #: The logical lines immediately above this one, nearest first. A decorator
    #: sits on its own line, so a rule about a `def` has to look up to see it.
    preceding: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisContext:
    """Facts about the whole change set that a single line cannot know."""

    changed_files: tuple[str, ...]
    change_set_has_test_file: bool


@dataclass(frozen=True)
class Rule:
    """A named check plus the metadata attached to every match it produces."""

    id: str
    category: str
    severity: str
    confidence: float
    languages: tuple[str, ...]
    detect: Callable[[LogicalLine, AnalysisContext], Optional[str]]


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    path: str
    line: int
    category: str
    severity: str
    confidence: float
    message: str
    evidence: str


@dataclass
class AnalysisResult:
    matches: list[RuleMatch] = field(default_factory=list)
    inspected_files: int = 0
    inspected_added_lines: int = 0
    unanchorable_files: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Lexing helpers
# --------------------------------------------------------------------------


def _scan_code(line: str) -> tuple[str, str, int]:
    """Split a physical line into code, string-masked code, and bracket depth.

    Characters inside a quoted string and everything after an unquoted ``#``
    are not code, so they never affect bracket depth. The second return value
    additionally empties every string literal, which is what a rule matches on
    when the text inside a string must not count as a match.
    """
    depth = 0
    quote: str | None = None
    out: list[str] = []
    masked: list[str] = []
    index = 0

    while index < len(line):
        char = line[index]

        if quote is not None:
            out.append(char)
            if char == "\\":
                if index + 1 < len(line):
                    out.append(line[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
                masked.append(char)
            index += 1
            continue

        if char in "\"'":
            quote = char
            out.append(char)
            masked.append(char)
            index += 1
            continue

        if char == "#":
            break

        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1

        out.append(char)
        masked.append(char)
        index += 1

    return "".join(out), "".join(masked), depth


def build_logical_lines(path: str, diff_file: DiffFile) -> list[LogicalLine]:
    """Group added right-side lines into statements, for rule matching."""
    return _logical_lines(path, diff_file.right_source, diff_file.right_lines)


def removed_logical_lines(path: str, block: ChangeBlock) -> list[LogicalLine]:
    """Group the removed lines of one change block the same way.

    Used to ask whether a construct a rule just matched was already present on
    the line this diff replaced.
    """
    return _logical_lines(path, block.removed, set(block.removed))


def _logical_lines(
    path: str, source: dict[int, str], reported: set[int]
) -> list[LogicalLine]:
    """Join continuation lines into statements anchored at their first reported line.

    ``source`` may hold context lines too; only a group containing at least one
    line from ``reported`` produces a result, and the anchor and evidence always
    come from that reported line rather than from wherever the statement began.
    """
    numbers = sorted(source)
    groups: list[tuple[list[int], str, str]] = []
    index = 0

    while index < len(numbers):
        depth = 0
        pieces: list[str] = []
        masked_pieces: list[str] = []
        members: list[int] = []
        cursor = index

        while cursor < len(numbers):
            number = numbers[cursor]
            if cursor > index and number != numbers[cursor - 1] + 1:
                break

            code, masked, delta = _scan_code(source[number])
            pieces.append(code.strip())
            masked_pieces.append(masked.strip())
            members.append(number)
            depth += delta
            cursor += 1
            if depth <= 0:
                break

        groups.append(
            (
                members,
                " ".join(piece for piece in pieces if piece),
                " ".join(piece for piece in masked_pieces if piece),
            )
        )
        index = cursor

    logical: list[LogicalLine] = []
    for position, (members, code, masked) in enumerate(groups):
        reported_members = [number for number in members if number in reported]
        if not reported_members:
            continue

        added_pieces = [_scan_code(source[number]) for number in reported_members]
        logical.append(
            LogicalLine(
                path=path,
                line=reported_members[0],
                code=code,
                code_outside_strings=masked,
                added_code=" ".join(part[0].strip() for part in added_pieces if part[0].strip()),
                added_code_outside_strings=" ".join(
                    part[1].strip() for part in added_pieces if part[1].strip()
                ),
                # Evidence is the first line this diff actually added. Taking it
                # from the start of the statement quoted unchanged context, which
                # both misled the report and broke evidence grounding.
                raw=source[reported_members[0]].rstrip(),
                following=tuple(group[1] for group in groups[position + 1 : position + 4]),
                preceding=tuple(
                    group[1] for group in reversed(groups[max(0, position - 3) : position])
                ),
            )
        )

    return logical


def _is_test_path(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    return (
        "tests" in lowered.split("/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _language_of(path: str) -> str:
    suffix = path.rsplit(".", 1)
    return suffix[-1].lower() if len(suffix) == 2 else ""


def _argument_slice(code: str, opener_end: int) -> str:
    """Return the text between the parenthesis at ``opener_end`` and its match."""
    depth = 0
    for offset in range(opener_end - 1, len(code)):
        char = code[offset]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return code[opener_end:offset]
    return code[opener_end:]


# --------------------------------------------------------------------------
# Rule detectors
# --------------------------------------------------------------------------

_BROAD_EXCEPT_RE = re.compile(r"^except\s*(BaseException\s*(as\s+\w+\s*)?)?:")
_EXCEPT_HEADER_RE = re.compile(r"^except\b.*:\s*(?P<body>.*)$")
_DEF_RE = re.compile(r"\bdef\s+(?P<name>\w+)\s*\(")
_MUTABLE_DEFAULT_RE = re.compile(r"=\s*(\[\s*\]|\{\s*\}|set\(\s*\)|list\(\s*\)|dict\(\s*\))")
_SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")
_EXECUTE_RE = re.compile(r"\.execute(?:many)?\s*\(")
_INTERPOLATION_RE = re.compile(r"""(^|[^\w])f["']|%|\.format\s*\(|\+""")
_SECRET_ASSIGN_RE = re.compile(
    r"""^(?:self\.)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[^=]+?)?=\s*"""
    r"""(?P<quote>['"])(?P<value>[^'"]{8,})(?P=quote)\s*$"""
)
_SECRET_NAME_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key)"
)
_PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)^(your[_-]|example|dummy|placeholder|changeme|change[_-]me|x{3,}|<|\.\.\.|"
    r"test[_-]?(key|token|secret)|fake[_-]|sk-xxx)"
)
_REQUESTS_CALL_RE = re.compile(r"\brequests\.(get|post|put|patch|delete|head|request)\s*\(")
_YAML_LOAD_RE = re.compile(r"\byaml\.load\s*\(")
_DYNAMIC_EVAL_RE = re.compile(r"(?<![\w.])(?P<call>eval|exec)\s*\(")
_ASSERT_RE = re.compile(r"^assert\b")
_TODO_RE = re.compile(r"(?i)#\s*(TODO|FIXME|XXX)\b")


def _detect_broad_except(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    if not _BROAD_EXCEPT_RE.match(line.code_outside_strings.strip()):
        return None
    return (
        "This handler catches every exception, including `KeyboardInterrupt` and "
        "`SystemExit`. Name the exception types this block can actually recover from."
    )


def _detect_swallowed_exception(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    match = _EXCEPT_HEADER_RE.match(line.code_outside_strings.strip())
    if match is None:
        return None

    body = match.group("body").strip()
    if body:
        swallowed = body == "pass"
    else:
        following = [text.strip() for text in line.following if text.strip()]
        swallowed = bool(following) and following[0] == "pass"

    if not swallowed:
        return None
    return (
        "The only statement in this except block is `pass`, so the error is discarded "
        "without being logged or re-raised."
    )


def _detect_mutable_default(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    match = _DEF_RE.search(line.code_outside_strings)
    if match is None:
        return None

    params = _argument_slice(line.code_outside_strings, match.end())
    default = _MUTABLE_DEFAULT_RE.search(params)
    if default is None:
        return None
    # The default has to be on a line this diff added. Editing one parameter of
    # a signature that already had `x=[]` does not introduce the shared default.
    if not _MUTABLE_DEFAULT_RE.search(line.added_code_outside_strings):
        return None
    return (
        f"`{match.group('name')}()` has a mutable default argument "
        f"(`{default.group(1)}`). It is created once at import time and shared by every "
        "call; default to `None` and build the value inside the function."
    )


def _detect_shell_true(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    if not _SHELL_TRUE_RE.search(line.code_outside_strings):
        return None
    return (
        "`shell=True` sends the command through a shell, so any interpolated value is "
        "parsed as shell syntax. Pass an argument list instead."
    )


def _detect_sql_interpolation(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    # Matched on the string-masked text on purpose: `%s` placeholders inside the
    # SQL literal are parameter markers, not Python formatting. Masking the
    # literal leaves only interpolation that happens outside it.
    match = _EXECUTE_RE.search(line.code_outside_strings)
    if match is None:
        return None

    argument = _argument_slice(line.code_outside_strings, match.end())
    if not _INTERPOLATION_RE.search(argument):
        return None
    return (
        "The SQL statement is assembled by string interpolation, so a value can change "
        "the statement itself. Pass the values as query parameters to `execute()`."
    )


def _detect_hardcoded_secret(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    match = _SECRET_ASSIGN_RE.match(line.code.strip())
    if match is None:
        return None

    name = match.group("name")
    value = match.group("value")
    if not _SECRET_NAME_RE.search(name):
        return None
    if _PLACEHOLDER_VALUE_RE.match(value) or "{" in value:
        return None
    return (
        f"`{name}` is assigned a credential-shaped literal. Read it from the environment "
        "or a secret store so it is not committed."
    )


def _detect_request_without_timeout(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    # Skipped in test files. This rule is about a production call blocking a
    # request thread; a test's HTTP call is bounded by the test runner, and on
    # a real corpus those matches outnumbered the production ones.
    if _is_test_path(line.path):
        return None

    match = _REQUESTS_CALL_RE.search(line.code_outside_strings)
    if match is None or "timeout" in line.code_outside_strings:
        return None
    return (
        f"`requests.{match.group(1)}()` is called without `timeout=`, so a slow or "
        "unresponsive peer can block this thread indefinitely."
    )


def _detect_yaml_unsafe_load(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    if not _YAML_LOAD_RE.search(line.code_outside_strings) or "Loader" in line.code_outside_strings:
        return None
    return (
        "`yaml.load()` without an explicit safe loader can construct arbitrary Python "
        "objects from the document. Use `yaml.safe_load()`."
    )


def _detect_dynamic_eval(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    match = _DYNAMIC_EVAL_RE.search(line.code_outside_strings)
    if match is None or "literal_eval" in line.code_outside_strings:
        return None
    return (
        f"`{match.group('call')}()` executes whatever string reaches it. Replace it with "
        "an explicit parser or a lookup table."
    )


def _detect_assert_for_validation(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    if _is_test_path(line.path) or not _ASSERT_RE.match(line.code_outside_strings.strip()):
        return None
    return (
        "`assert` is removed when Python runs with `-O`, so it should not be the only "
        "check protecting this path. Raise an explicit exception instead."
    )


def _detect_todo_marker(line: LogicalLine, _context: AnalysisContext) -> Optional[str]:
    match = _TODO_RE.search(line.raw)
    if match is None:
        return None
    return (
        f"A `{match.group(1).upper()}` marker is added here. Resolve it or link it to a "
        "tracked issue before this ships."
    )


RULES: tuple[Rule, ...] = (
    Rule("broad_except", "possible_bug", "medium", 0.8, ("py",), _detect_broad_except),
    Rule("swallowed_exception", "possible_bug", "medium", 0.75, ("py",), _detect_swallowed_exception),
    Rule("mutable_default_argument", "possible_bug", "medium", 0.85, ("py",), _detect_mutable_default),
    Rule("assert_for_validation", "possible_bug", "low", 0.5, ("py",), _detect_assert_for_validation),
    Rule("subprocess_shell_true", "security_reliability", "high", 0.85, ("py",), _detect_shell_true),
    Rule("sql_string_interpolation", "security_reliability", "high", 0.7, ("py",), _detect_sql_interpolation),
    Rule("hardcoded_secret", "security_reliability", "high", 0.65, ("py",), _detect_hardcoded_secret),
    Rule("dynamic_eval", "security_reliability", "high", 0.7, ("py",), _detect_dynamic_eval),
    Rule("yaml_unsafe_load", "security_reliability", "high", 0.8, ("py",), _detect_yaml_unsafe_load),
    Rule("request_without_timeout", "security_reliability", "medium", 0.6, ("py",), _detect_request_without_timeout),
    Rule("todo_marker", "recommended_action", "info", 0.9, ("py",), _detect_todo_marker),
)

RULE_IDS: tuple[str, ...] = tuple(rule.id for rule in RULES)

# When two rules describe the same line, keep only the more specific one. A
# swallowed `except:` is already a broad `except:`, and reporting both doubles
# the comment count without telling the reviewer anything new.
SUPPRESSED_BY: dict[str, tuple[str, ...]] = {
    "broad_except": ("swallowed_exception",),
}


def _suppress_overlaps(matches: list[RuleMatch]) -> list[RuleMatch]:
    present: dict[tuple[str, int], set[str]] = {}
    for match in matches:
        present.setdefault((match.path, match.line), set()).add(match.rule_id)

    return [
        match
        for match in matches
        if not present[(match.path, match.line)].intersection(
            SUPPRESSED_BY.get(match.rule_id, ())
        )
    ]


def _preexisting_rules(
    path: str, diff_file: DiffFile, context: AnalysisContext
) -> dict[int, frozenset[str]]:
    """For each change block, the rules that already matched its removed lines.

    This is what separates "this diff introduced the problem" from "this diff
    touched a line that already had the problem". On a real corpus the second
    case was the single largest source of false positives: a `# noqa` deleted
    from a bare `except:`, a version bound bumped inside an existing `assert`,
    a type hint relaxed in an existing signature.
    """
    result: dict[int, frozenset[str]] = {}

    for index, block in enumerate(diff_file.change_blocks):
        if not block.removed:
            continue
        matched: set[str] = set()
        for removed in removed_logical_lines(path, block):
            for rule in RULES:
                if rule.id in matched:
                    continue
                if rule.detect(removed, context) is not None:
                    matched.add(rule.id)
        if matched:
            result[index] = frozenset(matched)

    return result


def analyze_diff(diff: str, changed_files: list[str]) -> AnalysisResult:
    """Run every rule over the added lines of ``diff``.

    The result is ordered by file, line, then rule id, so two runs over the same
    diff produce byte-identical output.
    """
    diff_index = parse_unified_diff(diff)
    context = AnalysisContext(
        changed_files=tuple(changed_files),
        change_set_has_test_file=any(_is_test_path(path) for path in changed_files),
    )

    result = AnalysisResult()
    matches: list[RuleMatch] = []

    for path, diff_file in diff_index.files.items():
        if diff_file.is_binary or not diff_file.right_lines:
            continue

        result.inspected_files += 1
        result.inspected_added_lines += len(diff_file.right_lines)
        language = _language_of(path)

        preexisting = _preexisting_rules(path, diff_file, context)
        for line in build_logical_lines(path, diff_file):
            block_index = diff_file.block_of_added.get(line.line)
            for rule in RULES:
                if language not in rule.languages:
                    continue
                message = rule.detect(line, context)
                if message is None:
                    continue
                # The same construct was on the line this block replaced, so
                # this diff edited a line that already had it rather than
                # introducing it. Reviewing it here would be a comment about
                # someone else's earlier change.
                if rule.id in preexisting.get(block_index, frozenset()):
                    continue
                matches.append(
                    RuleMatch(
                        rule_id=rule.id,
                        path=path,
                        line=line.line,
                        category=rule.category,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        message=message,
                        evidence=line.raw.strip()[:MAX_EVIDENCE_CHARS],
                    )
                )

    result.matches = sorted(
        _suppress_overlaps(matches),
        key=lambda match: (match.path, match.line, match.rule_id),
    )
    result.unanchorable_files = sorted(
        path
        for path in changed_files
        if path not in diff_index.files or not diff_index.files[path].right_lines
    )
    return result
