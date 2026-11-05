"""Enclosing function/class context for provider review.

The provider is a fake that records the request, so these tests assert exactly
what the model would have been shown. No network call or API key is used.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Put backend/app on sys.path in the same way as the other application tests.
import evals.run_local  # noqa: F401
from change_context import MAX_CONTEXT_CHARS, build_change_context, enclosing_scopes
from diff_index import parse_unified_diff
from git_diff_reader import read_git_diff, read_post_change_sources
from llm_reviewer import (
    CONSISTENCY_SAMPLES_ENV,
    CONTEXT_CHARS_ENV,
    MAX_DIFF_CHARS,
    SYSTEM_PROMPT,
    build_provider_diff,
    review_diff,
)
from schemas import ProviderReview

SOURCE = '''\
import os


class Loader:
    """Loads files."""

    retries = 3

    def __init__(self, root):
        self.root = root

    @staticmethod
    def join(a, b):
        return os.path.join(a, b)

    def load(self, name):
        path = self.join(self.root, name)
        with open(path) as handle:
            data = handle.read()
        return data


def outer(values):
    def inner(value):
        return value * 2

    total = 0
    for value in values:
        total += inner(value)
    return total


LIMIT = 10
'''
SOURCE_LINES = SOURCE.splitlines()


def line_of(text: str) -> int:
    return next(i for i, line in enumerate(SOURCE_LINES, start=1) if line.strip() == text)


def diff_adding(path: str, source: str, added: list[int]) -> str:
    """A diff whose right side is ``source`` and whose added lines are ``added``.

    Every other line is context, so the diff is a valid view of ``source``.
    """
    lines = source.splitlines()
    removed_count = len(lines) - len(added)
    body = "".join(
        ("+" if number in added else " ") + text + "\n"
        for number, text in enumerate(lines, start=1)
    )
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{removed_count} +1,{len(lines)} @@\n{body}"
    )


def context_for(added: list[int], *, limit: int = MAX_CONTEXT_CHARS, source: str = SOURCE):
    index = parse_unified_diff(diff_adding("loader.py", source, added))
    return build_change_context(index, lambda paths: {"loader.py": source}, limit=limit)


# --- Scope selection ----------------------------------------------------------


def test_change_inside_a_method_sends_that_whole_method_only() -> None:
    context = context_for([line_of("data = handle.read()")])

    [scope] = context.scopes
    assert (scope.kind, scope.name) == ("def", "Loader.load")
    assert scope.start == line_of("def load(self, name):")
    assert scope.end == line_of("return data")
    assert "return data" in context.text
    assert "def __init__" not in context.text
    assert "class Loader" not in context.text


def test_change_in_a_class_body_sends_the_whole_class() -> None:
    context = context_for([line_of("retries = 3")])

    [scope] = context.scopes
    assert (scope.kind, scope.name) == ("class", "Loader")
    assert scope.end == line_of("return data")
    assert "def load(self, name):" in context.text


def test_decorator_belongs_to_its_function() -> None:
    [scope] = context_for([line_of("@staticmethod")]).scopes

    assert scope.name == "Loader.join"
    assert scope.start == line_of("@staticmethod")
    assert scope.end == line_of("return os.path.join(a, b)")


def test_two_changes_in_one_function_send_it_once() -> None:
    context = context_for([line_of("path = self.join(self.root, name)"), line_of("return data")])

    assert [scope.name for scope in context.scopes] == ["Loader.load"]


def test_change_in_nested_and_outer_function_sends_only_the_outer() -> None:
    context = context_for([line_of("return value * 2"), line_of("total = 0")])

    assert [scope.name for scope in context.scopes] == ["outer"]
    assert context.text.count("def inner(value):") == 1


def test_change_only_in_nested_function_sends_the_nested_function() -> None:
    assert [s.name for s in context_for([line_of("return value * 2")]).scopes] == ["outer.inner"]


def test_module_level_change_has_no_enclosing_scope() -> None:
    assert context_for([line_of("LIMIT = 10"), line_of("import os")]).scopes == ()


def test_scopes_are_rendered_with_post_change_line_numbers() -> None:
    [scope] = context_for([line_of("data = handle.read()")]).scopes
    rendered = scope.render().splitlines()

    assert rendered[0] == f"# loader.py:{scope.start}-{scope.end} def Loader.load"
    assert rendered[1].lstrip().startswith(f"{scope.start} | ")
    assert rendered[1].endswith("def load(self, name):")


def test_enclosing_scopes_raises_on_unparseable_source() -> None:
    with pytest.raises(SyntaxError):
        enclosing_scopes("def broken(:\n    pass\n", [1])


# --- Budget -------------------------------------------------------------------


def test_scope_over_the_budget_is_omitted_whole_never_cut() -> None:
    method = context_for([line_of("data = handle.read()")])
    one_scope = len(method.text)

    context = context_for(
        [line_of("data = handle.read()"), line_of("total = 0")], limit=one_scope + 1
    )

    assert [scope.name for scope in context.scopes] == ["Loader.load"]
    assert len(context.text) <= one_scope + 1
    assert context.omitted == (f"loader.py:{line_of('def outer(values):')}-"
                               f"{line_of('return total')} def outer",)
    assert "over the" in (context.detail() or "")


def test_total_rendered_context_never_exceeds_the_budget() -> None:
    added = [line_of(t) for t in ("retries = 3", "total = 0", "return value * 2")]
    for limit in range(0, 1200, 37):
        assert len(context_for(added, limit=limit).text) <= limit


def test_zero_budget_sends_nothing_and_reports_nothing() -> None:
    context = context_for([line_of("data = handle.read()")], limit=0)

    assert context.scopes == ()
    assert context.detail() is None


# --- Sources that cannot be trusted to match the diff --------------------------


def test_source_that_disagrees_with_the_diff_is_not_used() -> None:
    index = parse_unified_diff(diff_adding("loader.py", SOURCE, [line_of("return data")]))
    edited = SOURCE.replace("return data", "return data.strip()")

    context = build_change_context(index, lambda paths: {"loader.py": edited})

    assert context.scopes == ()
    assert context.unavailable == ("loader.py (does not match the diff)",)


def test_unreadable_and_unparseable_sources_are_reported() -> None:
    broken = "def broken(:\n    pass\n"
    diff = diff_adding("missing.py", SOURCE, [1]) + diff_adding("broken.py", broken, [2])

    context = build_change_context(
        parse_unified_diff(diff), lambda paths: {"broken.py": broken}
    )

    assert context.scopes == ()
    assert set(context.unavailable) == {"missing.py (not readable)", "broken.py (does not parse)"}


def test_non_python_files_are_never_requested() -> None:
    requested: list[list[str]] = []
    diff = diff_adding("README.md", "hello\nworld\n", [2]) + diff_adding(
        "loader.py", SOURCE, [line_of("return data")]
    )

    def load(paths: list[str]) -> dict[str, str]:
        requested.append(paths)
        return {"loader.py": SOURCE}

    build_change_context(parse_unified_diff(diff), load)

    assert requested == [["loader.py"]]


# --- Provider request -----------------------------------------------------------


def install_provider(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {"calls": 0, "users": []}

    class FakeResponses:
        def parse(self, **kwargs: object) -> SimpleNamespace:
            seen["calls"] = int(seen["calls"]) + 1
            messages = kwargs["input"]
            seen["system"] = messages[0]["content"]  # type: ignore[index]
            seen["user"] = messages[1]["content"]  # type: ignore[index]
            seen["users"].append(seen["user"])  # type: ignore[union-attr]
            return SimpleNamespace(
                output_parsed=ProviderReview(project_summary="Loads files.", findings=[])
            )

    class FakeOpenAI:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    monkeypatch.setenv("AI_REVIEW_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv(CONTEXT_CHARS_ENV, raising=False)
    monkeypatch.delenv(CONSISTENCY_SAMPLES_ENV, raising=False)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    return seen


def between(text: str, begin: str, end: str) -> str:
    start = text.index(begin) + len(begin)
    return text[start : text.index(end, start)]


def test_provider_receives_the_enclosing_function_inside_its_own_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = install_provider(monkeypatch)
    diff = diff_adding("loader.py", SOURCE, [line_of("data = handle.read()")])

    review = review_diff(diff, ["loader.py"], load_sources=lambda paths: {"loader.py": SOURCE})

    user = str(seen["user"])
    marker = user.split("BOUNDARY-", 1)[1].split(" ", 1)[0]
    diff_block = between(user, f"BOUNDARY-{marker} BEGIN\n", f"\nBOUNDARY-{marker} END")
    context_block = between(
        user, f"BOUNDARY-{marker} CONTEXT BEGIN\n", f"\nBOUNDARY-{marker} CONTEXT END"
    )
    assert diff_block == diff
    assert "def load(self, name):" in context_block
    assert "class Loader" not in context_block
    assert "CONTEXT block" in str(seen["system"])
    assert seen["system"] == SYSTEM_PROMPT
    assert review.review_status == "completed"
    assert review.provider_context == [
        f"loader.py:{line_of('def load(self, name):')}-{line_of('return data')} def Loader.load"
    ]
    assert review.review_status_detail is None


def test_every_sample_receives_the_same_context(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_provider(monkeypatch)
    monkeypatch.setenv(CONSISTENCY_SAMPLES_ENV, "3")
    diff = diff_adding("loader.py", SOURCE, [line_of("data = handle.read()")])

    review = review_diff(diff, ["loader.py"], load_sources=lambda paths: {"loader.py": SOURCE})

    users = seen["users"]
    assert seen["calls"] == 3
    assert len(set(users)) == 1  # type: ignore[arg-type]
    assert "def load(self, name):" in between(users[0], "CONTEXT BEGIN\n", "\nBOUNDARY-")  # type: ignore[index]
    assert review.provider_samples == 3
    assert len(review.provider_context) == 1


def test_without_sources_the_request_has_no_context_block(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_provider(monkeypatch)
    diff = diff_adding("loader.py", SOURCE, [line_of("data = handle.read()")])

    review = review_diff(diff, ["loader.py"])

    assert "CONTEXT BEGIN" not in str(seen["user"])
    assert review.provider_context == []


def test_static_mode_never_loads_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_REVIEW_MODE", "static")
    diff = diff_adding("loader.py", SOURCE, [line_of("data = handle.read()")])

    def explode(paths: list[str]) -> dict[str, str]:
        raise AssertionError("static mode read file sources")

    review = review_diff(diff, ["loader.py"], load_sources=explode)

    assert review.review_status == "completed"


def test_context_budget_setting_zero_disables_context(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install_provider(monkeypatch)
    monkeypatch.setenv(CONTEXT_CHARS_ENV, "0")
    diff = diff_adding("loader.py", SOURCE, [line_of("data = handle.read()")])

    review_diff(diff, ["loader.py"], load_sources=lambda paths: {"loader.py": SOURCE})

    assert "CONTEXT BEGIN" not in str(seen["user"])


@pytest.mark.parametrize("value", ["-1", "50001", "lots"])
def test_invalid_context_budget_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    seen = install_provider(monkeypatch)
    monkeypatch.setenv(CONTEXT_CHARS_ENV, value)

    review = review_diff(diff_adding("loader.py", SOURCE, [1]), ["loader.py"])

    assert seen["calls"] == 0
    assert review.review_status == "configuration_error"
    assert CONTEXT_CHARS_ENV in (review.review_status_detail or "")


# --- Truncation is unchanged by context -----------------------------------------

PADDING = [f"value_{i} = compute_something_reasonably_long({i})  # padding padding" for i in range(300)]
SECOND = "def handler(user_input):\n    cleaned = user_input.strip()\n    return eval(cleaned)\n"


def new_file(path: str, text: str) -> str:
    lines = text.splitlines()
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
    )


def test_omitted_file_contributes_no_context_and_stays_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = "\n".join(PADDING) + "\n"
    diff = new_file("a.py", first) + new_file("b.py", SECOND)
    assert len(diff) > MAX_DIFF_CHARS
    requested: list[list[str]] = []

    def load(paths: list[str]) -> dict[str, str]:
        requested.append(paths)
        return {"a.py": first, "b.py": SECOND}

    seen = install_provider(monkeypatch)
    review = review_diff(diff, ["a.py", "b.py"], load_sources=load)

    user = str(seen["user"])
    assert requested == [["a.py"]]
    assert "def handler" not in user
    assert "eval(cleaned)" not in user
    assert review.review_status == "incomplete"
    assert "not sent: b.py" in (review.review_status_detail or "")
    assert review.review_decision == "needs_human_review"
    # The static floor still covers the file the provider never saw.
    assert [f.file for f in review.findings if f.rule_id] == ["b.py"]


def test_partially_sent_file_gets_context_only_for_sent_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    early = "def early(x):\n    return x + 1\n"
    late = "def late(y):\n    return y - 1\n"
    source = early + "\n".join(PADDING) + "\n" + late
    diff = new_file("big.py", source)
    provider_diff = build_provider_diff(diff)
    assert provider_diff.partial_files == ("big.py",)

    seen = install_provider(monkeypatch)
    review = review_diff(diff, ["big.py"], load_sources=lambda paths: {"big.py": source})

    user = str(seen["user"])
    assert review.review_status == "incomplete"
    assert "partially sent: big.py" in (review.review_status_detail or "")
    assert [label.rsplit(" ", 1)[-1] for label in review.provider_context] == ["early"]
    assert "def late" not in user


def test_context_does_not_change_what_the_diff_budget_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = "def setup(x):\n    return x\n" + "\n".join(PADDING) + "\n"
    diff = new_file("a.py", first) + new_file("b.py", SECOND)

    seen = install_provider(monkeypatch)
    without = review_diff(diff, ["a.py", "b.py"])
    user_without = str(seen["user"])
    with_context = review_diff(
        diff, ["a.py", "b.py"], load_sources=lambda paths: {"a.py": first, "b.py": SECOND}
    )
    user_with = str(seen["user"])

    def sent_diff(user: str) -> str:
        marker = user.split("BOUNDARY-", 1)[1].split(" ", 1)[0]
        return between(user, f"BOUNDARY-{marker} BEGIN\n", f"\nBOUNDARY-{marker} END")

    assert "CONTEXT BEGIN" not in user_without
    assert "def setup(x):" in between(user_with, "CONTEXT BEGIN\n", "\nBOUNDARY-")
    assert with_context.provider_context == ["a.py:1-2 def setup"]
    assert sent_diff(user_with) == sent_diff(user_without) == build_provider_diff(diff).text
    assert without.review_status == with_context.review_status == "incomplete"
    assert without.review_status_detail == with_context.review_status_detail


# --- Reading post-change sources from git ---------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=30
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    x = 2\n    return x\n", encoding="utf-8")
    git(tmp_path, "commit", "-q", "-am", "head")
    return tmp_path


def test_commit_range_sources_come_from_head_not_the_working_tree(repo: Path) -> None:
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 'dirty'\n", encoding="utf-8")

    sources = read_post_change_sources(repo, ["pkg/mod.py"], head="HEAD")

    assert sources == {"pkg/mod.py": "def f():\n    x = 2\n    return x\n"}


def test_working_tree_sources_come_from_disk(repo: Path) -> None:
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 'dirty'\n", encoding="utf-8")

    sources = read_post_change_sources(repo, ["pkg/mod.py"])

    assert sources == {"pkg/mod.py": "def f():\n    return 'dirty'\n"}


def test_paths_outside_the_repository_are_not_read(repo: Path, tmp_path_factory) -> None:  # type: ignore[no-untyped-def]
    outside = tmp_path_factory.mktemp("outside") / "secret.py"
    outside.write_text("TOKEN = 'x'\n", encoding="utf-8")
    (repo / "link.py").symlink_to(outside)

    sources = read_post_change_sources(repo, ["link.py", "../outside/secret.py", "missing.py"])

    assert sources == {}


def test_commit_range_diff_and_sources_agree_end_to_end(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = read_git_diff(repo, base="HEAD~1", head="HEAD")
    seen = install_provider(monkeypatch)

    review = review_diff(
        result.diff,
        result.changed_files,
        load_sources=lambda paths: read_post_change_sources(repo, paths, head="HEAD"),
    )

    assert review.provider_context == ["pkg/mod.py:1-3 def f"]
    assert "1 | def f():" in str(seen["user"])
    assert "3 |     return x" in str(seen["user"])
