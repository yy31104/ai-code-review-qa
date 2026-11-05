"""Static checks on the GitHub Actions workflows.

Three properties are enforced for every workflow file:

- No ``${{ ... }}`` expression appears inside a ``run:`` script. GitHub expands
  expressions textually before the shell starts, so a value such as a dispatch
  input becomes shell source. Values must reach the script through ``env:``.
- Every ``uses:`` reference is pinned to a full 40-character commit SHA, so a
  moved or rewritten tag cannot change the code a job runs.
- Every ``actions/checkout`` step sets ``persist-credentials: false``. Otherwise
  the job token stays in ``.git/config`` for every later step, and any step
  that uploads or prints the checkout can leak it. No job here pushes or
  fetches after checkout, so none needs the stored credential.

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


def checkouts_keeping_credentials(text: str) -> list[int]:
    """Return line numbers of checkout steps without ``persist-credentials: false``."""
    lines = text.splitlines()
    missing: list[int] = []
    for number, line in enumerate(lines, start=1):
        match = re.match(r"^(\s*)(?:-\s+)?uses:\s*actions/checkout@", line)
        if match is None:
            continue
        key_indent = len(match.group(1)) if not line.lstrip().startswith("-") else _indent(line) + 2
        settings: list[str] = []
        following = lines[number:]
        if following and following[0].strip() == "with:" and _indent(following[0]) == key_indent:
            for setting in following[1:]:
                if setting.strip() and _indent(setting) <= key_indent:
                    break
                settings.append(setting.split("#", 1)[0].strip())
        if "persist-credentials: false" not in settings:
            missing.append(number)
    return missing


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


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_every_checkout_drops_its_credentials(workflow: Path) -> None:
    assert checkouts_keeping_credentials(workflow.read_text(encoding="utf-8")) == []


def test_credential_checker_flags_checkouts_that_keep_the_token() -> None:
    steps = (
        "      - name: Bare\n"
        "        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0\n"
        "      - name: Other settings only\n"
        "        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "      - name: Explicitly true\n"
        "        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0\n"
        "        with:\n"
        "          persist-credentials: true\n"
        "      - name: Safe\n"
        "        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0\n"
        "        with:\n"
        "          fetch-depth: 2\n"
        "          persist-credentials: false  # no later step needs the token\n"
        "      - name: Next step\n"
        "        with:\n"
        "          persist-credentials: false\n"
    )
    assert checkouts_keeping_credentials(steps) == [2, 4, 8]


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
