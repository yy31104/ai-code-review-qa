"""Static checks on the GitHub Actions workflows.

Two properties are enforced for every workflow file:

- No ``${{ ... }}`` expression appears inside a ``run:`` script. GitHub expands
  expressions textually before the shell starts, so a value such as a dispatch
  input becomes shell source. Values must reach the script through ``env:``.
- Every ``uses:`` reference is pinned to a full 40-character commit SHA, so a
  moved or rewritten tag cannot change the code a job runs.

The checks read the YAML as text; the CI environment installs no YAML parser.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

_RUN_KEY_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:\s*(?P<inline>.*)$")
_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)")
_PINNED_REF_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def run_blocks(text: str) -> list[tuple[int, str]]:
    """Return ``(first line number, script)`` for every ``run:`` step in ``text``."""
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        match = _RUN_KEY_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        key_indent = len(match.group("indent"))
        start = index + 1
        body = [match.group("inline")]
        index += 1
        while index < len(lines) and (
            not lines[index].strip() or _indent(lines[index]) > key_indent
        ):
            body.append(lines[index])
            index += 1
        blocks.append((start, "\n".join(body)))
    return blocks


def expression_lines_in_run_blocks(text: str) -> list[int]:
    hits: list[int] = []
    for start, script in run_blocks(text):
        for offset, line in enumerate(script.splitlines()):
            if "${{" in line:
                hits.append(start + offset)
    return hits


def unpinned_uses(text: str) -> list[str]:
    refs: list[str] = []
    for line in text.splitlines():
        match = _USES_RE.match(line)
        if match is None:
            continue
        ref = match.group("ref")
        if ref.startswith("./"):
            continue
        if not _PINNED_REF_RE.match(ref):
            refs.append(ref)
    return refs


def test_workflows_are_present() -> None:
    names = {path.name for path in WORKFLOWS}
    assert {"pr-comment.yml", "pr-summary.yml", "evals.yml", "ai-review.yml"} <= names


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_no_expression_is_spliced_into_a_run_script(workflow: Path) -> None:
    assert expression_lines_in_run_blocks(workflow.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_every_action_is_pinned_to_a_commit_sha(workflow: Path) -> None:
    text = workflow.read_text(encoding="utf-8")
    assert "uses:" in text
    assert unpinned_uses(text) == []


def test_checker_flags_the_pattern_this_change_removed() -> None:
    """The checks must fail on the shapes they exist to catch."""
    spliced = (
        "      - name: Validate pull number\n"
        "        run: |\n"
        '          if [ -z "${{ inputs.pull_number }}" ]; then\n'
        "            exit 1\n"
        "          fi\n"
        "      - name: Safe\n"
        "        env:\n"
        "          PULL_NUMBER: ${{ inputs.pull_number }}\n"
        '        run: echo "$PULL_NUMBER"\n'
    )
    assert expression_lines_in_run_blocks(spliced) == [3]

    floating = (
        "      - uses: actions/checkout@v6\n"
        "      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0\n"
        "      - uses: actions/upload-artifact@main\n"
        "      - uses: ./.github/actions/local\n"
    )
    assert unpinned_uses(floating) == ["actions/checkout@v6", "actions/upload-artifact@main"]


def _validate_pull_number_script() -> str:
    text = (ROOT / ".github" / "workflows" / "pr-comment.yml").read_text(encoding="utf-8")
    lines = text.splitlines()
    name_line = next(
        index for index, line in enumerate(lines) if line.strip() == "- name: Validate pull number"
    )
    start, script = next(block for block in run_blocks(text) if block[0] > name_line + 1)
    assert start > name_line
    return script.lstrip("|").strip("\n")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("22", True),
        ("1", True),
        ("", False),
        ("0", False),
        ("022", False),
        ("22 ", False),
        ("-1", False),
        ("22/comments?per_page=1", False),
        ('1"; touch "$MARKER"; echo "', False),
        ("$(touch $MARKER)", False),
    ],
)
def test_validate_pull_number_step_accepts_only_positive_integers(
    tmp_path: Path, value: str, accepted: bool
) -> None:
    marker = tmp_path / "pwned"
    result = subprocess.run(
        ["bash", "-e", "-c", _validate_pull_number_script()],
        env={"PULL_NUMBER": value, "MARKER": str(marker), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert (result.returncode == 0) is accepted, result.stderr
    assert not marker.exists()
